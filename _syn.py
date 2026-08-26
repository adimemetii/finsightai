import os
from jinja2 import Environment, TemplateSyntaxError
env = Environment(autoescape=True)
templates = ["base.html","index.html","login.html","signup.html","dashboard.html","upload.html","predict.html","history.html","analytics.html","powerbi.html","404.html","500.html"]
for t in templates:
    p = os.path.join("templates", t)
    try:
        with open(p, encoding="utf-8") as f:
            src = f.read()
        env.from_string(src)
        print(t, "OK")
    except TemplateSyntaxError as e:
        lineno = getattr(e, "lineno", "?")
        lines = src.splitlines()
        before = lines[max(0,lineno-3):lineno] if isinstance(lineno,int) else []
        print(t, "ERR at line", lineno, ":", e.message)
        for i,l in enumerate(before, start=max(1,lineno-2)):
            print("   ", i, repr(l[:200]))
    except Exception as e:
        print(t, "ERR", type(e).__name__, str(e))
print("SCAN DONE")