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
import shlex
import time
import json
import hashlib
import base64
import tempfile
import rfc6266
import re

BASE_DIR = os.path.expanduser(os.path.join('~', '.dq'))
CONFIG_FILE = os.path.join(BASE_DIR, 'config.yaml')
CONFIG_DEFAULTS = {
    'queue': os.path.join(BASE_DIR, 'queue.txt'),
    'dest': os.path.join('~', 'Downloads'),
    'state': os.path.join(BASE_DIR, 'state.json'),
    'failed': os.path.join(BASE_DIR, 'failed.txt'),
    'completed': os.path.join(BASE_DIR, 'completed.txt'),
    'auth': {},
    'verbose': False,
    'curlargs': [],
    'poll': 10.0,
    'retries': 5,
    'post': None,
}
CURL_RANGE_ERROR = 33
CURL_HTTP_ERROR = 22
CURL_BASE = ["curl", "--location-trusted", "--fail"]
PART_EXT = 'part'
FILENAME_REPLACE = [
    re.compile(r'[\\/]'),
    re.compile(r'^\.'),
    re.compile(r'[\x00-\x1f]'),
    re.compile(r'[<>:"\?\*\|]'),
    re.compile(r'\.$'),
]

LOG = logging.getLogger('dq')
LOG.addHandler(logging.StreamHandler())
LOG.propagate = False

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

def add_line(filename, line):
    """Add a single line of text to a text file. Ensures that the file's
    parent directory exists before trying to write.
    """
    ensure_parent(filename)
    with open(filename, 'a') as f:
        print >>f, line

def hashstr(s):
    """Return the (cryptographic) hash of the input string as another
    string. The string is safe to include in a filename. The hash is
    mangled slightly so it's probably best not to use it for actual
    security.
    """
    return base64.b64encode(hashlib.sha256(s).digest(), 'xx')[:-1]

def unique_path(path):
    """Returns a version of ``path`` that does not exist on the
    filesystem. Specifically, if ``path` itself already exists, then
    something unique is appended to the path.
    """
    if not os.path.exists(path):
        return path

    base, ext = os.path.splitext(path)
    match = re.search(r'\.(\d)+$', base)
    if match:
        num = int(match.group(1))
        base = base[:match.start()]
    else:
        num = 0
    while True:
        num += 1
        new_path = '%s.%i%s' % (base, num, ext)
        if not os.path.exists(new_path):
            return new_path

def _filename(url, headers):
    """Given the URL and the HTTP headers received while fetching it,
    generate a reasonable name for the file. If no suitable name can be
    found, return None. (Either uses the Content-Disposition explicit
    filename or a filename from the URL.)
    """
    filename = None

    # Try to get filename from Content-Disposition header.
    heads = re.findall(r'^Content-Disposition:\s*(.*?)\r\n',
                       headers, re.I | re.M)
    if heads:
        cdisp = rfc6266.parse_headers(heads[-1], relaxed=True)
        filename = cdisp.filename_unsafe

    # Get filename from URL.
    if not filename:
        parts = urlparse.urlparse(url).path.split('/')
        if parts:
            filename = parts[-1]

    # Strip unsafe characters from path.
    if filename:
        filename = filename.strip()
        for sep in (os.sep, os.altsep):
            if sep:
                filename = filename.replace(sep, '_')
        for pat in FILENAME_REPLACE:
            filename = pat.sub('_', filename)
        if filename:
            return filename


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
    if (value is None or value == '') and key != 'post':
        raise ValueError('no such config key: %s' % key)

    if key in ('queue', 'dest', 'state', 'failed', 'completed'):
        value = os.path.abspath(os.path.expanduser(value))
        if key == 'dest' and not os.path.isdir(value):
            raise UserError('destination directory %s does not exist' % value)
    elif key in ('auth',) and not isinstance(value, dict):
        LOG.warn('%s must be a dictionary' % key)
        value = {}
    elif key in ('curlargs',) and not isinstance(value, list):
        value = shlex.split(value)
    return value

def run_hook(url, path):
    """Run the user's post-download command."""
    command = _config('post')
    if not command:
        return
    command = os.path.expanduser(command)
    subprocess.call([command, path, url])


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
            add_line(_config('failed'), url)
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

    add_line(_config('completed'), url)

def set_current(url):
    """Record the currently-downloading URL in the state file. If URL is
    None, the current URL is cleared.
    """
    with State() as s:
        if url is None:
            if 'current' in s:
                del s['current']
        else:
            s['current'] = {'url': url}

def get_current():
    """Determine the currently-downloading URL. Return None if none is
    recorded. The URL must be in the queue; if it is not, then it is cleared
    and None is returned.
    """
    queue = get_queue()
    with State() as s:
        if 'current' in s:
            url = s['current']['url']
            if url in queue:
                return url
            else:
                del s['current']


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
    auth_pats = _config('auth')
    for auth_pat, auth_parts in auth_pats.iteritems():
        if auth_pat in url:
            break
    else:
        # No matching authentication entry found.
        return []

    # Splice the details into the URL.
    username, password = auth_parts.split(None, 1)
    return ['-u', '%s:%s' % (username, password)]

def fetch(url):
    """Fetch a URL. On success, return the path to the downloaded file.
    On failure, return None.
    """
    LOG.info("fetching %s" % url)

    urlhash = hashstr(url)
    outfile = os.path.join(_config('dest'),
                           '{}.{}'.format(urlhash, PART_EXT))
    headerfile = os.path.join(tempfile.gettempdir(),
                              'dq_headers_{}'.format(urlhash))

    args = CURL_BASE + ["--output", outfile,
                        "--dump-header", headerfile]
    if os.path.exists(outfile):
        args += ["--continue-at", "-"]
    args += _authentication(url)
    args += _config('curlargs')
    args += [url]

    while True:
        LOG.debug("curl fetch command: %s" % ' '.join(args))
        res = subprocess.call(args)
        LOG.debug('curl exit code: %i' % res)

        if res == CURL_RANGE_ERROR:
            # Tried to resume, but the server does not support ranges
            # (resuming). Overwrite file.
            LOG.error("resume failed; starting over")
            args.remove("--continue-at")
            args.remove("-")
            continue
        elif res == CURL_HTTP_ERROR:
            LOG.error("download failed")
            return None

        if res:
            return None
        else:
            break

    # Move the file to the final filename.
    with open(headerfile) as f:
        headers = f.read()
    dest_file = _filename(url, headers)
    if dest_file:
        dest_file = os.path.join(_config('dest'), dest_file)
        dest_file = unique_path(dest_file)
        os.rename(outfile, dest_file)
        return dest_file
    else:
        return outfile


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
    cur_url = get_current()
    try:
        while True:
            if not cur_url:
                cur_url = _wait_for_url()
            while cur_url is not None:
                set_current(cur_url)
                dest_path = fetch(cur_url)
                if dest_path:
                    LOG.info("downloaded to {}".format(dest_path))
                    run_hook(cur_url, dest_path)
                    record_success(cur_url)
                    remove = True
                else:
                    remove = record_failure(cur_url)
                cur_url = _next_url(cur_url, remove)
            set_current(None)
    except KeyboardInterrupt:
        pass

def do_web():
    """Web command: start Flask Web interface."""
    import dqweb
    dqweb.app.run(host='0.0.0.0')

def dq(command=None, *args):
    """Main command-line interface."""
    if not command:
        LOG.error('available commands: add, list, run, web')
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
    elif command.startswith('w'):
        do_web()

def main():
    code = dq(*sys.argv[1:])
    if code:
        sys.exit(code)
