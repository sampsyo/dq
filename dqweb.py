import flask
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
<form method="POST" target="">
    <input type="text" name="url" style="width: 25em;">
    <input type="submit" value="Add URL">
</form>

</body>
"""

@app.route("/", methods=['GET', 'POST'])
def home():
    if flask.request.method == 'POST':
        url = flask.request.form['url']
        dq.enqueue([url])

    return flask.render_template_string(TEMPLATE,
        urls=dq.get_queue(),
        current=dq.get_current(),
    )

if __name__ == "__main__":
    app.run(host='0.0.0.0')
