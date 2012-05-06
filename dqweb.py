import flask
import os
import dq
app = flask.Flask(__name__)

TEMPLATE = """
<!DOCTYPE html>
<head>
    <title>dq</title>
</head>
<body>

<h2>Queue</h2>
<ul>
    {% for url in urls %}
        <li>
            <code>{{ url }}</code>
            {% if url == current %}
                (downloading)
            {% endif %}
        </li>
    {% else %}
        <li>Queue is empty.</li>
    {% endfor %}
</ul>

<h2>Add a URL</h2>
<form method="POST" action="/add">
    <input type="text" name="url" style="width: 25em;">
    <input type="submit" value="Add URL">
</form>

<h2>Completed</h2>
<ul>
    {% for url in completed %}
    <li><code>{{ url }}</code></li>
    {% endfor %}
</ul>

<h2>Failed</h2>
<ul>
    {% for url in failed %}
    <li><code>{{ url }}</code></li>
    {% endfor %}
</ul>

</body>
"""

def _lines(filename):
    """Get a list of lines from a file if it exists. If the file does
    not exist, return [].
    """
    if os.path.exists(filename):
        with open(filename) as f:
            return f.readlines()
    else:
        return []

@app.route("/")
def home():
    return flask.render_template_string(TEMPLATE,
        urls=dq.get_queue(),
        current=dq.get_current(),
        failed=_lines(dq._config('failed')),
        completed=_lines(dq._config('completed')),
    )

@app.route("/add", methods=['POST'])
def add_url():
    url = flask.request.form['url']
    dq.enqueue([url])
    return flask.redirect(flask.url_for('home'))

if __name__ == "__main__":
    app.run(host='0.0.0.0')
