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
import re
import urlparse
import random
import string

CONFIG_FILE = os.path.expanduser(os.path.join('~', '.dqconfig'))
CONFIG_DEFAULTS = {
    'queue': os.path.join('~', '.dqlist'),
    'dest': os.path.join('~', 'Downloads'),
    'auth': {},
    'verbose': False,
}
CURL_RANGE_ERROR = 33
CURL_BASE = ["curl", "--location-trusted"]

LOG = logging.getLogger('dq')
LOG.addHandler(logging.StreamHandler())

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
    elif key in ('auth',) and not isinstance(value, dict):
        LOG.warn('%s must be a dictionary' % key)
        value = {}
    return value

def get_queue():
    qfile = _config('queue')
    if not os.path.exists(qfile):
        return []
    with AtomicFile(qfile) as f:
        return list(_read_queue(f))

def enqueue(urls):
    with AtomicFile(_config('queue'), 'a') as f:
        for url in urls:
            print >>f, url

def get_dest(url):
    """Get the destination filename for a URL."""
    filename = None

    # First, try sending a HEAD request to look for a
    # "Content-Disposition" header containing a filename.
    args = CURL_BASE + ["-Is", url]
    args += _authentication(url)
    try:
        out = subprocess.check_output(args)
    except subprocess.CalledProcessError:
        pass
    else:
        match = re.search(r'content-disposition:.*filename\s*=\s*'
                          r'["\']?([^"\']+)["\']?', out, re.I)
        if match:
            filename = os.path.basename(match.group(1))
            LOG.debug('got filename from headers: %s' % filename)

    # Next, guess the filename from the URL.
    if not filename:
        parts = urlparse.urlparse(url).path.split('/')
        if parts:
            filename = parts[-1]
            LOG.debug('got filename from URL: %s' % filename)

    # Fall back on a nonsense filename.
    if not filename:
        filename = 'download-%s' % random_string()
        LOG.debug('using random: %s' % filename)

    return filename

def _authentication(url):
    """Returns additional cURL parameters for authenticating the given
    URL. Returns an empty list if no authentication is necessary.
    """
    # Add authentication to the URL if necessary.
    host = urlparse.urlparse(url).netloc
    auth_hosts = _config('auth')
    for auth_host, auth_parts in auth_hosts.iteritems():
        if host in auth_host:
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
    outfile = get_dest(url)

    args = CURL_BASE + ["-o", outfile]
    args += _authentication(url)
    if os.path.exists(outfile):
        # Try to resume.
        args += ["-C", "-"]
    args += [url]

    while True:
        res = subprocess.call(args)
        
        if res == CURL_RANGE_ERROR:
            # Tried to resume, but the server does not support ranges
            # (resuming). Overwrite file.
            print >>sys.stderr, "resume failed; starting over"
            args.remove("-C")
            args.remove("-")
            continue

        if res:
            return False
        else:
            return True

def do_list():
    for url in get_queue():
        print url

def do_add(urls):
    for url in urls:
        assert url.startswith('http://')
    enqueue(urls)

def do_run():
    queue = get_queue()
    if not queue:
        return
    url = queue[0]

    if fetch(url):
        # Remove the completed URL.
        with AtomicFile(_config('queue'), 'r+') as f:
            queue = list(_read_queue(f))
            f.seek(0)
            f.truncate()
            for q_url in queue:
                if q_url != url:
                    print >>f, q_url

def dq(command=None, *args):
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
        if not args:
            LOG.error('specify URLs to add')
            return 1
        do_add(args)
    elif command.startswith('r'):
        do_run()

if __name__ == '__main__':
    code = dq(*sys.argv[1:])
    if code:
        sys.exit(code)
