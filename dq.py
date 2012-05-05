"""A very simple, command-line-controlled, curl-powered, auto-resuming,
auto-retrying download manager.

The download queue is a simple text file. Every line in ~/.dqlist is a
URL to be downloaded; the first URL in the list will be downloaded
first. To add to the queue, you can just append to this file or use dq's
"add" command.
"""
import fcntl
import sys
import os
import subprocess
import yaml
import logging
import urlparse
import random
import string
import contextlib
import shlex
import time
import json

BASE_DIR = os.path.expanduser(os.path.join('~', '.dq'))
CONFIG_FILE = os.path.join(BASE_DIR, 'config.yaml')
CONFIG_DEFAULTS = {
    'queue': os.path.join(BASE_DIR, 'queue.txt'),
    'dest': os.path.join('~', 'Downloads'),
    'state': os.path.join(BASE_DIR, 'state.json'),
    'failed': os.path.join(BASE_DIR, 'failed.txt'),
    'auth': {},
    'verbose': False,
    'curlargs': [],
    'poll': 10.0,
    'retries': 5,
}
CURL_RANGE_ERROR = 33
CURL_HTTP_ERROR = 22
CURL_BASE = ["curl", "--location-trusted", "--fail"]

LOG = logging.getLogger('dq')
LOG.addHandler(logging.StreamHandler())

class UserError(Exception):
    """Raised when the program is misconfigured."""


# Utilities.

def random_string(length=20, chars=(string.ascii_letters + string.digits)):
    return ''.join(random.choice(chars) for i in range(length))

class AtomicFile(file):
    def __init__(self, path, mode='r'):
        super(AtomicFile, self).__init__(path, mode)
        if 'r' in mode and '+' not in mode:
            # Reading: acquire a reader (shared) lock.
            fcntl.lockf(self, fcntl.LOCK_SH)
        else:
            # Writing: acquire an exclusive lock.
            fcntl.lockf(self, fcntl.LOCK_EX)

    def close(self):
        if not self.closed:
            fcntl.lockf(self, fcntl.LOCK_UN)  # Unlock.
        super(AtomicFile, self).close()

def ensure_parent(path):
    """Ensure that the parent directory of the given path exists.
    """
    parent = os.path.dirname(path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent)

@contextlib.contextmanager
def chdir(d):
    """A context manager that changes the working directory and then
    changes it back.
    """
    old_dir = os.getcwd()
    os.chdir(d)
    yield
    os.chdir(old_dir)


# Reading the configuration file.

def _config(key, path=CONFIG_FILE):
    """Read the configuration dictionary, with default values filled
    in.
    """
    try:
        with open(path) as f:
            config = yaml.load(f)
    except IOError:
        LOG.debug('configuration file is unreadable')
        config = {}
    except yaml.YAMLError:
        LOG.warn('malformed configuration file')
        config = {}
    if not isinstance(config, dict):
        LOG.warn('configuration is not a YAML dictionary')
        config = {}

    value = config.get(key, CONFIG_DEFAULTS.get(key))
    if value is None or value == '':
        raise ValueError('no such config key: %s' % key)

    if key in ('queue', 'dest', 'state', 'failed'):
        value = os.path.abspath(os.path.expanduser(value))
        if key == 'dest' and not os.path.isdir(value):
            raise UserError('destination directory %s does not exist' % value)
    elif key in ('auth',) and not isinstance(value, dict):
        LOG.warn('%s must be a dictionary' % key)
        value = {}
    elif key in ('curlargs',) and not isinstance(value, list):
        value = shlex.split(value)
    return value


# State management.

class State(object):
    """A context manager providing atomic access to the JSON data stored
    in a state file. The resulting value is a dictionary reflecting the
    state data. While the context is active, the data may be modified;
    the changed data is written back to the state file on exit.
    """
    def __init__(self, path=None):
        self.path = path or _config('state')

    def __enter__(self):
        ensure_parent(self.path)
        if os.path.exists(self.path):
            self.af = AtomicFile(self.path, 'r+')
            try:
                self.data = json.load(self.af)
            except ValueError:
                LOG.debug('state file could not be parsed')
                self.data = {}
            except IOError:
                LOG.debug('state file is unreadable')
                self.data = {}
            else:
                if not isinstance(self.data, dict):
                    LOG.debug('state file is not a dictionary')
                    self.data = {}
        else:
            self.af = AtomicFile(self.path, 'w')
            self.data = {}
        return self.data

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.af.seek(0)
        self.af.truncate()
        json.dump(self.data, self.af)
        self.af.close()

def record_failure(url):
    """Increment the number of failed tries for a URL. Returns a boolean
    indicating whether the URL has failed *permanently*, indicating that
    it should be removed from the queue.
    """
    max_retries = _config('retries')

    # Increment the try count.
    with State() as s:
        if 'tries' in s:
            if url in s['tries']:
                s['tries'][url] += 1
            else:
                s['tries'][url] = 1
        else:
            s['tries'] = {url: 1}

        # Check whether we've reached the maximum.
        if s['tries'][url] >= max_retries:
            del s['tries'][url]
            LOG.warn('retried {} times, giving up'.format(max_retries))

            # Record URL in list of permanent failures.
            failed_fn = _config('failed')
            ensure_parent(failed_fn)
            with open(failed_fn, 'a') as f:
                print >>f, url

            return True

        else:
            return False

def record_success(url):
    """Eliminate the try counter for a URL, indicating that is has been
    successfully transferred.
    """
    with State() as s:
        if 'tries' in s and url in s['tries']:
            del s['tries'][url]


# Queue logic.

def _read_queue(fh):
    """Generates the URLs in the queue."""
    for line in fh:
        line = line.strip()
        if line:
            yield line

def get_queue():
    qfile = _config('queue')
    if not os.path.exists(qfile):
        return []
    with AtomicFile(qfile) as f:
        return list(_read_queue(f))

def enqueue(urls):
    queue_fn = _config('queue')
    ensure_parent(queue_fn)
    with AtomicFile(queue_fn, 'a') as f:
        for url in urls:
            print >>f, url

def _next_url(cur_url, remove):
    """Gets the next URL in the queue. If `remove`, then the current URL
    is (atomically) removed from the queue (i.e., when the fetch
    succeeds). If no next URL is available, None is returned.
    """
    with AtomicFile(_config('queue'), 'r+') as f:
        queue = list(_read_queue(f))

        # Get the next URL.
        if cur_url in queue:
            cur_index = queue.index(cur_url)

            # Remove current URL.
            if remove:
                # Remove *all* instances of this URL (tolerate
                # duplicates).
                queue = [u for u in queue if u != cur_url]
                if queue:
                    if cur_index >= len(queue):
                        cur_index = 0
                    next_url = queue[cur_index]
                else:
                    next_url = None

            # Failure: just skip to next URL.
            else:
                cur_index += 1
                if cur_index >= len(queue):
                    cur_index = 0
                next_url = queue[cur_index]

        # URL has since vanished from the queue.
        else:
            if queue:
                next_url = queue[0]
            else:
                next_url = None

        # Write back new queue (if necessary).
        if remove:
            f.seek(0)
            f.truncate()
            for q_url in queue:
                print >>f, q_url

    return next_url

def _wait_for_url():
    """If the queue has any URLs in it, return the first URL. Otherwise,
    poll the file until it becomes nonempty and then return the first
    URL.
    """
    poll_time = _config('poll')
    queue_filename = _config('queue')

    mtime = os.path.getmtime(queue_filename)
    queue = get_queue()
    while not queue:
        while os.path.getmtime(queue_filename) == mtime:
            time.sleep(poll_time)
        mtime = os.path.getmtime(queue_filename)
        queue = get_queue()
    return queue[0]


# Fetch logic.

def _authentication(url):
    """Returns additional cURL parameters for authenticating the given
    URL. Returns an empty list if no authentication is necessary.
    """
    # Add authentication to the URL if necessary.
    host = urlparse.urlparse(url).netloc
    auth_hosts = _config('auth')
    for auth_host, auth_parts in auth_hosts.iteritems():
        if auth_host in host:
            break
    else:
        # No matching authentication entry found.
        return []

    # Splice the details into the URL.
    username, password = auth_parts.split(None, 1)
    return ['-u', '%s:%s' % (username, password)]

def fetch(url):
    """Fetch a URL. Returns True on success and False on failure."""
    LOG.info("fetching %s" % url)

    args = CURL_BASE + ["-O", "-J", "-C", "-"]
    args += _authentication(url)
    args += _config('curlargs')
    args += [url]

    while True:
        LOG.debug("curl fetch command: %s" % ' '.join(args))
        with chdir(_config('dest')):
            res = subprocess.call(args)
        LOG.debug('curl exit code: %i' % res)

        if res == CURL_RANGE_ERROR:
            # Tried to resume, but the server does not support ranges
            # (resuming). Overwrite file.
            LOG.error("resume failed; starting over")
            args.remove("-C")
            args.remove("-")
            continue
        elif res == CURL_HTTP_ERROR:
            LOG.error("download failed")
            return False

        if res:
            return False
        else:
            return True


# Main command-line interface.

def do_list():
    """List command: show the queue."""
    for url in get_queue():
        print url

def do_add(urls):
    """Add command: enqueue a URL."""
    if not urls:
        raise UserError("no URLs specified")
    enqueue(urls)

def do_run():
    """Run command: execute the download queue."""
    try:
        while True:
            cur_url = _wait_for_url()
            while cur_url is not None:
                success = fetch(cur_url)
                if success:
                    record_success(cur_url)
                    remove = True
                else:
                    remove = record_failure(cur_url)
                cur_url = _next_url(cur_url, remove)
    except KeyboardInterrupt:
        pass

def dq(command=None, *args):
    """Main command-line interface."""
    if not command:
        LOG.error('available commands: add, list, run')
        return 1

    if _config('verbose'):
        LOG.setLevel(logging.DEBUG)
    else:
        LOG.setLevel(logging.INFO)

    if command.startswith('l'):
        do_list()
    elif command.startswith('a'):
        do_add(args)
    elif command.startswith('r'):
        do_run()

def main():
    code = dq(*sys.argv[1:])
    if code:
        sys.exit(code)
