dq: a dead-simple download queue manager
========================================

`dq` is a very simple download manager that operates on the command line. You
give it URLs; it downloads them one at a time using [cURL][]. That's it.

Obligatory feature bullet points:

* The queue is just a text file: one URL per line. To add files to the queue,
  you can just append to the text file; to see the queue, all you need is `cat`.
  (Convenient commands are also provided.)
* Automatically resume transfers when possible.
* Configurable HTTP authentication: set a per-domain username and password so
  you don't have to enter your details every time.
* Configurable destination directory.
* Supports all the protocols that [cURL][] supports.

Basic Usage
-----------

The URL queue is stored at `~/.dqlist` and files are downloaded to
`~/Downloads` by default. (Both of these are configurable; see below.) To add a
URL to your queue, just append to `~/.dqlist` or use the `dq add` command:

    $ dq add http://example.com/file http://example.com/file2 [...]

To see your queue, type `dq list` (or `cat ~/.dqlist` if you prefer). Then, to
start working through your queue, run the `dq run` command. This will download
everything in your queue, starting withe the first entry in the file. The
command exits once it has tried to download each file. URLs are only removed
from the queue file once they are successfully and completely downloaded.

Configuration
-------------

The configuration file is at `~/.dqconfig`. It is a [YAML][] document. The
available configuration keys are:

* `dest`: The download destination directory.
* `queue`: The URL queue text file path.
* `auth`: A dictionary mapping domain names to usernames and passwords for HTTP
  basic authentication. If a URL in the queue contains a given key, the username
  and password (separated by whitespace) given in the value are used for
  authentication.
* `verbose`: A boolean indicating whether debug output should be shown.

Here's an example configuration file:

    dest: ~/incoming
    queue: ~/downloadqueue.txt
    auth:
        example.com: username password
    verbose: true 

To Do
-----

I'll do these things eventually:

* Continually running daemon. Currently, you have to leave `dq run` running to
  fetch your files and restart it after your queue finishes. There should be a
  daemon that sits in waiting even if there are currently no files to download.
* Automatically retry when a download is interrupted.
* Multiple downloads?
* Error log.

About
-----

`dq` is by Adrian Sampson, who wasn't able to find a simple,
command-line-oriented download manager that didn't suck. The code is available
under the [MIT license][].

[curl]: http://curl.haxx.se/
[yaml]: http://yaml.org/
[mit license]: http://www.opensource.org/licenses/mit-license.php
