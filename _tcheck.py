import os, traceback
import app as A
for name in A.app.jinja_env.list_templates():
    try:
        A.app.jinja_env.get_template(name)
        print(name, "OK")
    except Exception as e:
        print(name, "ERR", type(e).__name__, e)
print("ALL DONE")
