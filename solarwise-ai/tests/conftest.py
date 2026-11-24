import os, sys, tempfile, pathlib
os.environ["RETRIEVER"] = "lexical"
os.environ["GEMINI_API_KEY"] = ""
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
