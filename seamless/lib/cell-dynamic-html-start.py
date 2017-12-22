import jinja2
from urllib.parse import urlsplit, urlunsplit

params = PINS.DYNAMIC_HTML_PARAMS.get()
tmpl = PINS.DYNAMIC_HTML_TEMPLATE.get()
vars_ = []
vars_full = []
for var_name, v in params["vars"].items():
    vv = v[0]
    vars_.append(vv)
    chars = ".[]{}"
    full_var = True
    for char in chars:
        if char in vv:
            full_var = False
            break
    if full_var:
        vars_full.append(vv)
dynamic_html0 = jinja2.Template(tmpl).render(
    {"vars": vars_, "vars_full": vars_full}
)

from seamless.websocketserver import websocketserver
websocketserver.start() #no-op if the websocketserver has already started

public_address = websocketserver.public_address
websocketserver_uri_tuple0 = urlsplit( public_address)
if not websocketserver_uri_tuple0.netloc:
    websocketserver_uri_tuple0 = urlsplit("http://" + public_address.lstrip("/"))
port = websocketserver.public_port
if port is None:
    try:
        port = websocketserver_uri_tuple0.port
    except ValueError:
        pass
    if port is None:
        port = ''
netloc = websocketserver_uri_tuple0.netloc
if port and port != 80:
    netloc = netloc + ":" + str(port)
websocketserver_uri_tuple = ("ws", netloc,
  websocketserver_uri_tuple0.path,
  websocketserver_uri_tuple0.query,
  websocketserver_uri_tuple0.fragment
 )
websocketserver_uri = urlunsplit(websocketserver_uri_tuple)
dynamic_html = jinja2.Template(dynamic_html0).render({
    "IDENTIFIER": IDENTIFIER,
    "WEBSOCKETSERVER_URI": websocketserver_uri,
    "WEBSOCKETSERVER_ADDRESS": websocketserver.public_address,
    "WEBSOCKETSERVER_PORT": websocketserver.public_port,
})
PINS.dynamic_html.set(dynamic_html)

def update(on_start):
    do_evals = set()
    for var_name in params["vars"]:
        pin = getattr(PINS, var_name)
        if not pin.updated:
            continue
        value = pin.get()
        var, evals = params["vars"][var_name]
        msg = {"type":"var", "var": var, "value": value}
        #print("MSG", msg, IDENTIFIER)
        websocketserver.send_message(IDENTIFIER, msg)
        for e in evals:
            do_evals.add(e)
    for html_name in params["html"]:
        pin = getattr(PINS, html_name)
        if not pin.updated:
            continue
        value = pin.get()
        id_ = params["html"][html_name]
        msg = {"type":"html", "id": id_, "value": value}
        #print("MSG", msg, IDENTIFIER)
        websocketserver.send_message(IDENTIFIER, msg)
    for e in params["evals"]:
        do_eval = False
        do_on_start = params["evals"][e]
        if on_start:
            if do_on_start == True:
                do_eval = True
            elif do_on_start == False:
                do_eval = False
            else:
                do_eval = (e in do_evals)
        else:
            do_eval = (e in do_evals)
        if do_eval:
            pin = getattr(PINS, e)
            value = pin.get()
            msg = {"type":"eval", "value": value}
            #print("MSG", msg, IDENTIFIER)
            websocketserver.send_message(IDENTIFIER, msg)

update(on_start=True)
