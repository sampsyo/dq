dq: a dead-simple download queue manager
========================================

`dq` is a very simple download manager that operates on the command line. You
give it URLs; it downloads them one at a time using [cURL][]. That's it.

Obligatory feature bullet points:

* The queue is just a text file: one URL per line. To add files to the queue,
  you can just append to the text file; to see the queue, all you need is `cat`.
  (Convenient commands are also provided.)
* Automatically resume transfers when possible.
* Retry failed downloads up to *N* times.
* Post-download hooks: run a shell command when a download finishes.
* Configurable HTTP authentication: set a per-domain username and password so
  you don't have to enter your details every time.
* Can run as a daemon, automatically checking for new URLs to fetch in the
  background.
* Configurable destination directory.
* Supports all the protocols that [cURL][] supports.
* Includes a really simple Web interface.

Basic Usage
-----------

The URL queue is stored at `~/.dq/queue.txt` and files are downloaded to
`~/Downloads` by default. (Both of these are configurable; see below.) To add a
URL to your queue, just append to `~/.dq/queue.txt` or use the `dq add`
command:

    $ dq add http://example.com/file http://example.com/file2 [...]

To see your queue, type `dq list` (or `cat ~/.dq/queue.txt` if you prefer).
Then, to start working through your queue, run the `dq run` command. This will
download everything in your queue, starting with the first entry in the file.
If there are no URLs in the queue currently, the process waits for a new URL to
be added.

URLs are only removed from the queue file once they are successfully and
completely downloaded. The downloader will retry failed downloads up to five
times (this limit is configurable). If a URL fails to download after five
tries, it is removed from the queue and placed in `~/.dq/failed.txt`.

While the downloader is running, you can continue to add URLs to the queue
file. The downloader process will pick them up automatically and start
downloading them. To avoid messy concurrent filesystem access, use `dq add`
instead of modifying the `queue.txt` file directly.

Type `^C` to exit the downloader.

Configuration
-------------

The configuration file is at `~/.dq/config.yaml`. It is a [YAML][] document.
The available configuration keys are:

* `dest`: The download destination directory.
* `queue`: The URL queue text file path.
* `auth`: A dictionary mapping domain names to usernames and passwords for HTTP
  basic authentication. If a URL in the queue contains a given key, the username
  and password (separated by whitespace) given in the value are used for
  authentication.
* `post`: A shell command to be executed when a download completes
  successfully. The string `<URL>` is replaced with the URL in question.
* `curlargs`: Additional command-line arguments to be passed to curl.
* `retries`: The number of times to try downloading a given URL before giving
  up.

These configuration keys are less likely to be useful but are available just in case:

* `verbose`: A boolean indicating whether debug output should be shown.
* `poll`: The number of seconds between polls of the queue file when it is
  empty.
* `state`: The JSON file to store state in (e.g., the number of failed download
  attempts per URL).
* `failed`: The file where failed downloads are recorded. After a URL exceeds
  its retry count, it is removed from the queue and placed here.

Here's an example configuration file:

    dest: ~/incoming
    queue: ~/downloadqueue.txt
    auth:
        example.com: username password
    poll: 10.0
    post: >
        osascript -e 'tell app "System Events" to
        display dialog "Finished download: <URL>"' &

Web Interface
-------------

There's an extremely simple Web interface to the download queue in `dqweb.py`.
It's a [Flask][flask] app that just displays the current queue and provides a
form field for enqueueing new URLs. Just run `python dqweb.py` after installing
Flask and visit `http://127.0.0.1:5000` in your browser.

[flask]: http://flask.pocoo.org/

To Do
-----

I'll do these things eventually:

* Multiple concurrent downloads?
* Error log.
* Use inotify instead of polling the queue file.

About
-----

`dq` is by Adrian Sampson, who wasn't able to find a simple,
command-line-oriented download manager that didn't suck. The code is available
under the [MIT license][].

[curl]: http://curl.haxx.se/
[yaml]: http://yaml.org/
[mit license]: http://www.opensource.org/licenses/mit-license.php
