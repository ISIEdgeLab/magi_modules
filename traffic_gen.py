from string import letters
from flask import Flask, Response, request

app = Flask(__name__)

@app.route('/')
def index():
    msg = '''
Supported URLs:
  /gettext/<number> - returns that many ascii bytes.
  /gethost - returns HTML document displaying client names.
'''
    return Response(msg, 200)

@app.route('/gettext/<size>')
def gettext_size(size):
    return gettext(size)

@app.route('/getsize.py', methods=['get'])
def getsize_py():
    size = int(request.args.get('length'))
    return gettext(size)

textbuf = letters * 64 # don't really know a good size for this.
def gettext(size):
    global textbuf

    try:
        size = int(size)
    except ValueError:
        return Response('Bad size: {}'.format(size))

    def generate_text():
        for _ in xrange(size/len(textbuf)):
            yield textbuf

        yield textbuf[0:size%len(textbuf)]
    
    resp = Response(generate_text(), mimetype='text/plain')
    resp.headers.add('Content-Length', str(size))
    return resp

@app.route('/gethost')
def gethost():
    addr = request.remote_addr
    names = []
    with open('/etc/hosts', 'r') as fd:
        for l in fd.readlines():
            if l.startswith(addr):
                names = l.split()[1:]
                break
        else:
            names = ['UNKNOWN']

    ret = '<html><body><p>\n'
    ret += 'Source is node named {} with IP {}\n'.format(', '.join(names), addr)
    ret += '</p></body></html>\n'

    return Response(ret)

@app.route('/gethost.py')   # backwards compat.
def gethost_py():
    return gethost()

if __name__ == '__main__':
    app.run(host='0.0.0.0')

