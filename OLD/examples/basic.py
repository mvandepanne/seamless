#!/usr/bin/env ipython

"""Basic example

Sets up a transformer that computes a result based on two inputs
The input and the transformation code is edited live in a GUI
"""

from seamless import context, cell, pythoncell, transformer
from seamless.lib import edit, display

ctx = context()

# Create 3 int cells: a=2, b=3, and result
ctx.a = cell("int").set(2)
ctx.b = cell("int").set(3)
ctx.result = cell("int")

# Set up a transformer that computes "result" as a function of "a" and "b"
t = ctx.transform = transformer({
    "a": {"pin": "input", "dtype": "int"},
    "b": {"pin": "input", "dtype": "int"},
    "result": {"pin": "output", "dtype": "int"}
})

# Connect the cells to the transformer pins
ctx.a.connect(t.a)
ctx.b.connect(t.b)
t.result.connect(ctx.result)

# Every transformer has an implicit extra input pin, called "code"
# It must be connected to a Python cell
ctx.formula = pythoncell().set("return a + b")
ctx.formula.connect(t.code)

# Transformers execute asynchronously; ctx.equilibrate() will wait until all
#  transformations have finished
ctx.equilibrate()

# The result cell will now have been computed
print(ctx.result.value)  # 5

# Updating either input automatically recomputes the result
ctx.a.set(10)
ctx.b.set(20)
ctx.equilibrate()
print(ctx.result.value)  # 30

# Updating the code also automatically recomputes the result
ctx.formula.set("""
def fibonacci(n):
    def fib(n):
        if n <= 1:
            return [1]
        elif n == 2:
            return [1, 1]
        else:
            fib0 = fib(n-1)
            return fib0 + [ fib0[-1] + fib0[-2] ]
    fib0 = fib(n)
    return fib0[-1]
return fibonacci(a) + fibonacci(b)
""")
ctx.equilibrate()
print(ctx.result.value)  # 6820

# The inputs and the result and code can be edited/shown in a GUI
#  This automatically recomputes the result
ctx.gui = context()  # Create a subcontext to organize our cells better
ctx.gui.a = edit(ctx.a, "Input a")
ctx.gui.b = edit(ctx.b, "Input b")
ctx.gui.result = display(ctx.result, "Result")

# Same for the code, this creates a text editor
# In this case, the code is updated as soon as you press Ctrl+S or click "Save"
ctx.gui.formula = edit(ctx.formula, "Transformer code")

# The source code of each editor is itself a seamless cell that can be edited
# Editing its source code immediately changes the other window!
text_editor_code = ctx.gui.formula.rc.code_start.cell()
ctx.gui.text_editor = edit(text_editor_code, "Text editor source code")

# The entire context can be saved in a file
ctx.tofile("basic.seamless")

# The context can later be loaded with:
#   ctx = seamless.fromfile("basic.seamless")
