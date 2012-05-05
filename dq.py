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

BASE_DIR = os.path.expanduser(os.path.join('~', '.dq'))
CONFIG_FILE = os.path.join(BASE_DIR, 'config.yaml')
CONFIG_DEFAULTS = {
    'queue': os.path.join(BASE_DIR, 'queue.txt'),
    'dest': os.path.join('~', 'Downloads'),
    'auth': {},
    'verbose': False,
    'curlargs': [],
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
            fcntl.lockf(self, fcntl.LOCK_UN) # Unlock.
        super(AtomicFile, self).close()


# Queue and configuration logic.

def _read_queue(fh):
    """Generates the URLs in the queue."""
    for line in fh:
        line = line.strip()
        if line:
            yield line

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

    if key in ('queue', 'dest'):
        value = os.path.abspath(os.path.expanduser(value))
        if key == 'dest' and not os.path.isdir(value):
            raise UserError('destination directory %s does not exist' % value)
    elif key in ('auth',) and not isinstance(value, dict):
        LOG.warn('%s must be a dictionary' % key)
        value = {}
    elif key in ('curlargs',) and not isinstance(value, list):
        value = shlex.split(value)
    return value

def get_queue():
    qfile = _config('queue')
    if not os.path.exists(qfile):
        return []
    with AtomicFile(qfile) as f:
        return list(_read_queue(f))

def enqueue(urls):
    queue_fn = _config('queue')
    queue_parent = os.path.dirname(queue_fn)
    if not os.path.exists(queue_parent):
        os.makedirs(queue_parent)
    with AtomicFile(queue_fn, 'a') as f:
        for url in urls:
            print >>f, url

@contextlib.contextmanager
def chdir(d):
    """A context manager that changes the working directory and then
    changes it back.
    """
    old_dir = os.getcwd()
    os.chdir(d)
    yield
    os.chdir(old_dir)

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
    queue = get_queue()
    if queue:
        cur_url = queue[0]
        while cur_url is not None:
            success = fetch(cur_url)
            cur_url = _next_url(cur_url, success)

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
