
"""
Spyder consists of three parts:
- A schema format (.spy) to define rich data models (validation, inline formatting definitions)
- A toolchain to convert a data model defined in .spy into a Python class,
   together with meta-information (dependency list, type schema tree, form tree)
- A data format (.web) to define instances of data models. Spyder Python classes can automatically
  parse the format


The toolchain applies the following transformations
.spy => SpyXML
SpyXML => Spyder dependency list
SpyXML => JSON schema
  subset of JSON schema
  Fully expanded to the level of Spyder primitives.
  Spyder primitives are mapped, e.g Integer => int
SpyXML => code sheet (validation rules and their error messages, extra methods) (JSON)
SpyXML => form instructions (XML)
JSON schema + validation sheet => Python class

This leads to the following ATC definitions:

Conversions:

("spyder", "schema")            => ("xml", "schema", "spyder")
("xml", "schema", "spyder")     =>
    ("spyder", "depslist") +
    ("json", "schema") +
    ("json", "codesheet", "python") +
    ("xml", "schema", "form")
("json", "schema") +
("json", "codesheet", "python")   =>
    ("code", "python")

Operations:
("code", "python") => register(typename)
("xml",  "schema", "form")  => register(typename)
("spyder", "depslist") => register(typename)
The schema is a class attribute of the Spyder class
The form() class attribute of the Spyder class returns dynamically the registered form

ATC chains:

- Primary chain
atc.operator (
    (open("Bla.spy").read(),
    ("spyder", "schema"),
    "register",
    "Bla"
)

- Only as data:
result = atc.convert
    (open("Bla.spy").read(),
    ("spyder", "schema"),
    [
        ("spyder", "depslist"),
        ("json", "codesheet", "python"),
        ("json", "schema"),
        ("xml",  "schema", "form"),
    ]
)

The way to import is *always* from seamless.spyder import MySpyderModel
There is a Spyder import hook;
whenever there is a statement "from seamless.spyder import Bla",
  the dependencies of Bla and then Bla itself is being imported from Bla.spy
NOTE: whenever a Spyder type is updated, all of its dependencies are regenerated
 the old classes are marked as invalid:
 this is a class attribute against which certain methods check (__init__, validate)
 and an Exception is raised if they are used


TODO:
- eliminate string parsing, simplify File (only file and format, no more modes)
- get rid of ObjectList
    Do make a little convenience function to load and save a Python list/dict of Spyder objects. No nesting.
- don't forget Resource and Array
- No more .fly
- __copydict__ with defaults:
    x = (0,0,0)
    if isinstance(_a, dict) and "x" in _a
  =>
    try:
        x = _a["x"]
    except ValueError:
        x = (0,0,0)
- set method
- numpy dtype:
    - available as .dtype() class method
    - int for Integer, bool for Boolean, etc.
      SpyderType.dtype() for SpyderType; numpy supports nested dtypes!
- fromnumpy constructor:
    Does not work on Spyder objects that contain Arrays, Files, Resources, or optional attributes
    Strings are expanded to 255 char arrays (like old Pascal).
        If x is a String attribute, 'self.x = "spam"'' is changed to self.x[:] = "spam" +  ('\0' * 255)[len("spam"):]
    On a single object:
    - Stores the argument to .fromnumpy as the inner numpy object
    - all attributes become now accessors/properties into the numpy object
    On Arrays (also ArrayArrays, ArrayArrayArrays):
    - fromnumpy determines length of numpy array, fromnumpy is then forwarded to every element
    - __setitem__ becomes overridden, changing a[0] = spam to a[0].set(spam)
    - __setslice__, same (is this necessary?)
    - append, pop becomes forbidden

    Any Spyder object constructed with .fromnumpy has a .numpy method to retrieve the inner numpy object
    Calling .numpy on a Spyder object NOT constructed with .fromnumpy is not possible
      However, there is a .tonumpy method, with two different behaviors:
        For Spyder objects constructed with .fromnumpy, .tonumpy is an alias of .numpy
        For other Spyder objects, a numpy array is constructed according to
            If the Spyder object is an Array (or ArrayArray etc.), the shape is determined using max(len(X)) for dimension

    In the future: storage {} blocks :
    - to support non-255 string storage
    - support Arrays, Files, Resources, optionals
    - make the Spyder model numpy-only; i.e. after standard construction, return self.type.fromnumpy(self.tonumpy())

"""

max_array_depth = 2


reserved_types = (
  "Spyder",
  "Type",
  "Object",
  "Delete",
  "Include",
  "None",
  "True",
  "False",
)

reserved_endings = (
  "Error",
  "Exit",
  "Exception",
)

reserved_membernames = (
  "type", "typename", "length", "name",
  "convert", "cast", "validate",
  "data", "str", "repr", "dict", "fromfile", "tofile",
  "listen", "block", "unblock", "buttons", "form",
  "invalid",
)


def is_valid_spydertype(type_name):
    """Tests if a string is a valid Spyder type"""
    if not type_name.replace("_", "x").isalnum():
        return False

    if not type_name[0].isupper():
        return False

    if len(type_name) > 1 and type_name == type_name.upper():
        return False

    if type_name.endswith("Array") or type_name in reserved_types:
        return False

    for ending in reserved_endings:
        if type_name.endswith(ending):
            return False
    return True


def is_valid_spydertype2(type_name):
    """Tests if a string is a valid Spyder type. Endings with Array are also allowed
    """
    array_depth = 0

    while type_name.endswith("Array"):
        type_name = type_name[:-len("Array")]
        array_depth += 1

    if array_depth > max_array_depth: # Why 3?
        return False

    return is_valid_spydertype(type_name)


from . import manager, parse
import sys


def init():
    pass
