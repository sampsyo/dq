"""A very simple, command-line-controlled, curl-powered, auto-resuming,
auto-retrying download manager.
"""
import fcntl
import sys
import os
import subprocess

QUEUE_FILE = os.path.expanduser(os.path.join('~', '.dqlist'))
DEST_DIR = os.path.expanduser(os.path.join('~', 'Downloads'))

CURL_RANGE_ERROR = 33

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
    for line in fh:
        line = line.strip()
        if line:
            yield line

def get_queue():
    if not os.path.exists(QUEUE_FILE):
        return []
    with AtomicFile(QUEUE_FILE) as f:
        return list(_read_queue(f))

def enqueue(urls):
    with AtomicFile(QUEUE_FILE, 'a') as f:
        for url in urls:
            print >>f, url

def get_dest(url):
    return os.path.join(DEST_DIR, 'out.txt')

def fetch(url):
    """Fetch a URL. Returns True on success and False on failure."""
    print >>sys.stderr, "fetching %s" % url
    outfile = get_dest(url)

    args = ["curl", "-o", outfile]
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
        with AtomicFile(QUEUE_FILE, 'r+') as f:
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
