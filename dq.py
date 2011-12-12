"""A very simple, command-line-controlled, curl-powered, auto-resuming,
auto-retrying download manager.
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
}

CURL_RANGE_ERROR = 33

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
        logging.debug('configuration file is unreadable')
        config = {}
    except yaml.YAMLError:
        logging.warn('malformed configuration file')
        config = {}
    if not isinstance(config, dict):
        logging.warn('configuration is not a YAML dictionary')
        config = {}

    value = config.get(key) or CONFIG_DEFAULTS.get(key)
    if not value:
        raise ValueError('no such config key: %s' % key)

    if key in ('queue', 'dest'):
        value = os.path.abspath(os.path.expanduser(value))
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
    # First, try sending a HEAD request to look for a
    # "Content-Disposition" header containing a filename.
    filename = None
    try:
        out = subprocess.check_output(["curl", "-LI", url])
    except subprocess.CalledProcessError:
        pass
    else:
        match = re.search(r'content-disposition:.*filename\s*=\s*'
                          r'["\']?([^"\']+)["\']?', out, re.I)
        if match:
            filename = os.path.basename(match.group(1))

    # Next, guess the filename from the URL.
    if not filename:
        parts = urlparse.urlparse(url).path.split('/')
        if parts:
            filename = parts[-1]

    # Fall back on a nonsense filename.
    if not filename:
        filename = 'download-%s' % random_string()

    return filename

def fetch(url):
    """Fetch a URL. Returns True on success and False on failure."""
    print >>sys.stderr, "fetching %s" % url
    outfile = get_dest(url)

    args = ["curl", "-L", "-o", outfile]
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
        print >>sys.stderr, 'available commands: add, list, run'
        sys.exit(1)

    if command.startswith('l'):
        do_list()
    elif command.startswith('a'):
        if not args:
            print >>sys.stderr, 'specify URLs to add'
        do_add(args)
    elif command.startswith('r'):
        do_run()

if __name__ == '__main__':
    dq(*sys.argv[1:])
