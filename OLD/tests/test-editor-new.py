import os
import sys
import time

from seamless import context, cell, transformer, reactor, macro
ctx = context()

@macro({"formula": {"type": "str", "default": "return value*2"}})
def operator(ctx, formula ):
    from seamless import cell, transformer
    tparams = ctx.tparams = cell("object").set(
    {
      "value": {
        "pin": "input",
        "dtype": "int"
      },
      "output": {
        "pin": "output",
        "dtype": "int"
      }
    }
    )

    cont = ctx.cont = transformer(tparams)
    c_code = cont.code.cell()
    c_code.set(formula)
    ctx.export(cont)




eparams = ctx.eparams = cell("object").set(
{
  "value": {
    "pin": "edit",
    "dtype": "int"
  },
  "title": {
    "pin": "input",
    "dtype": "str"
  },
}
)

teparams = {
  "value": {
    "pin": "edit",
    "dtype": "str"
  },
  "title": {
    "pin": "input",
    "dtype": "str"
  },
}

teparams2 = {
  "value": {
    "pin": "edit",
    "dtype": ("text", "code", "python")
  },
  "title": {
    "pin": "input",
    "dtype": "str"
  },
}

op = operator()
c_data = op.value.cell()
c_data.set(4)
c_output = op.output.cell()
c_code = op.cont.code.cell()

ctx.equilibrate()
print("VALUE", c_data.data, "'" + c_code.data + "'", c_output.data)

c_data.set(5)
c_code.set("return value*3")

ctx.equilibrate()
print("VALUE", c_data.data, "'" + c_code.data + "'", c_output.data)

@macro("json",with_context=False)
def make_editor(eparams):
    from seamless import reactor
    rc = reactor(eparams)
    rc.code_start.cell().fromfile("test-editor_pycell.py")
    rc.code_stop.cell().set('w.destroy()')
    rc.code_update.cell().set("""
if PINS.value.updated:
    b.setValue(PINS.value.get())
if PINS.title.updated:
    w.setWindowTitle(PINS.title.get())
""")
    return rc

@macro("json",with_context=False)
def make_text_editor(eparams):
    from seamless import cell, reactor
    rc = reactor(eparams)
    rc.code_start.cell().fromfile("test-editor_pycell2.py")
    rc.code_stop.cell().set('w.destroy()')
    rc.code_update.cell().set("""
if PINS.value.updated:
    b.setText(PINS.value.get())
if PINS.title.updated:
    w.setWindowTitle(PINS.title.get())
""")
    return rc

ed1 = ctx.ed1 = make_editor(eparams)
ed2 = ctx.ed2 = make_editor(eparams)
ed1.title.cell().set("Editor #1")
ed2.title.cell().set("Editor #2")
c_data.connect(ed1.value)
c_output.connect(ed2.value)

#ted1 = ctx.ted1 = make_text_editor(teparams)
ted1 = ctx.ted1 = make_text_editor(teparams2)
ted1.title.cell().set("Formula editor")
#c = ed1.title.cell()
c = c_code
#v = ted1.value.cell()
#v.set("Test!!")
c.connect(ted1.value)

meta_ted = ctx.meta_ted = make_text_editor(teparams2)
meta_ted.title.cell().set("Meta-editor")
c = ted1.code_start.cell()
#v = meta_ted1.value.cell()
#v.set("Test!!")
c.connect(meta_ted.value)
