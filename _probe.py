import traceback
try:
    import app as A
    print("IMPORT OK")
    for path, view_kwargs in [
        ("/", {}),
        ("/login", {}),
        ("/signup", {}),
        ("/dashboard", {}),
        ("/upload", {}),
        ("/analytics", {}),
        ("/predict", {}),
        ("/history", {}),
        ("/powerbi", {}),
        ("/nonexistent-xyz", {}),
    ]:
        try:
            with A.app.test_client() as c:
                r = c.get(path, follow_redirects=False)
                print(path, "->", r.status_code)
                if r.status_code >= 500 or 'UndefinedError' in r.get_data(as_text=True) or 'jinja' in r.get_data(as_text=True).lower():
                    print("  ^ CHECK TEMPLATE")
        except Exception as e:
            print(path, "EXC:", type(e).__name__, e)
except Exception as e:
    print("IMPORT FAIL")
    traceback.print_exc()
