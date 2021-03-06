"""
Takes a JSON dict of source object definitions (source code strings, language
property, compiler options, etc.)
 and generates a dict-of-binary-objects (.o / .obj)
Linking options etc. are passed through.
This dict can be sent to a transformer or reactor over a binarymodule pin

Current state: stub.
Fleshed-out spec is in:
    TODO.md, section Modules, compiled workers, and interpreted workers

TODO: Make this a high-level library of transformers
For now, to dynamically add a language/compiler,
  modify the languages/compilers dicts
"""

import os
from ..core.protocol import cson2json
mydir = os.path.abspath(os.path.split(__file__)[0])
compilers_cson_file = os.path.join(mydir, "compilers.cson")
languages_cson_file = os.path.join(mydir, "languages.cson")
with open(compilers_cson_file) as f:
    compilers_cson = f.read()
compilers = cson2json(compilers_cson)
with open(languages_cson_file) as f:
    languages_cson = f.read()
languages = cson2json(languages_cson)

def find_language(lang):
    try:
        language = languages[lang]
        extension = language.get("extension")
        if isinstance(extension, list):
            extension = extension[0]
    except KeyError:
        ext_to_lang = {}
        for lang0, language in languages.items():
            ext = language.get("extension", [])
            if isinstance(ext, str):
                if ext == lang:
                    break
            else:
                if lang in ext:
                    break
        else:
            raise KeyError(lang) from None
        extension = lang
    return lang, language, extension

from .compile import compile
