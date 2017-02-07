from collections import OrderedDict
from seamless import editor, macro
from seamless.core.cell import Cell
from seamless.core.context import active_context_as

@macro(OrderedDict((
    ("display_type","str"),
    ("title",{"type": "str", "default": "Basic display"})
)))
def basic_display(ctx, display_type, title):
    from seamless import editor

    _displays = {
      "int": {
        "code": "cell-basic_display_int.py",
        "update": "cell-basic_display_UPDATE.py",
      },
      "float": {
        "code": "cell-basic_display_float.py",
        "update": "cell-basic_display_UPDATE.py",
      },
      "text": {
        "code": "cell-basic_display_text.py",
        "update": "cell-basic_display_text_UPDATE.py",
      },
      "json": {
        "code": "cell-basic_display_json.py",
        "update": "cell-basic_display_json_UPDATE.py",
      },
    }

    def _match_type(type, typelist):
        typelist = list(typelist)
        type2 = type
        if isinstance(type, str):
            type2 = (type,)
        typelist2 = []
        for t in typelist:
            if isinstance(t, str):
                typelist2.append((t,))
            else:
                typelist2.append(t)
        matches = []
        for n in range(len(typelist)):
            ltype = typelist2[n]
            k = min(len(type2), len(ltype))
            if type2[:k] == ltype[:k]:
                matches.append((n, k))
        if not len(matches):
            raise TypeError("Cannot find display for cell type '{0}'".format(type))
        matches.sort(key=lambda v: -v[1])
        bestmatch = matches[0][0]
        return typelist[bestmatch]

    display_type = _match_type(display_type, _displays.keys())
    pinparams = {
      "value": {
        "pin": "input",
        "dtype": display_type
      },
      "title": {
        "pin": "input",
        "dtype": "str",
      },
    }
    d = ctx.display = editor(pinparams)
    d.title.cell().set(title)
    d.code_start.cell().fromfile(_displays[display_type]["code"])
    d.code_stop.cell().set('w.destroy()')
    upfile = _displays[display_type]["update"]
    c_up = d.code_update.cell()
    if upfile is not None:
        c_up.fromfile(upfile)
    else:
        c_up.set("")
    ctx.export(d, forced=["title"])

def display(cell, title=None, own=True):
    assert isinstance(cell, Cell)
    assert cell.context is not None
    d = basic_display(cell.dtype, title)
    cell.connect(d.value)
    if own:
        cell.own(d)
    return d
