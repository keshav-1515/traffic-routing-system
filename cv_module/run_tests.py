import importlib
import sys
import types

print('Running tests by importing test module directly')
# Provide lightweight mocks for optional heavyweight dependencies the test module imports
if 'flask' not in sys.modules:
    class _DummyApp:
        def __init__(self, *a, **k):
            pass
        def route(self, *a, **k):
            def _dec(f):
                return f
            return _dec
        def run(self, *a, **k):
            return None
    dummy_flask = types.SimpleNamespace(Flask=_DummyApp, jsonify=lambda x: x, render_template=lambda *a, **k: '', request=types.SimpleNamespace(get_json=lambda **k: {}))
    sys.modules['flask'] = dummy_flask
if 'cv2' not in sys.modules:
    sys.modules['cv2'] = types.SimpleNamespace()
if 'numpy' not in sys.modules:
    sys.modules['numpy'] = types.SimpleNamespace()
try:
    m = importlib.import_module('tests.test_incidents')
except Exception as e:
    print('Failed to import tests.test_incidents:', e)
    sys.exit(2)
try:
    m2 = importlib.import_module('tests.test_cv')
except Exception as e:
    print('Failed to import tests.test_cv:', e)
    sys.exit(2)
try:
    m3 = importlib.import_module('tests.test_tracker')
except Exception as e:
    print('Failed to import tests.test_tracker:', e)
    sys.exit(2)
try:
    m4 = importlib.import_module('tests.test_graph_mode')
except Exception as e:
    print('Failed to import tests.test_graph_mode:', e)
    sys.exit(2)

failed = False
for module in (m, m2, m3, m4):
    for name in dir(module):
        if name.startswith('test_'):
            func = getattr(module, name)
            if callable(func):
                try:
                    print('RUN:', module.__name__, name)
                    func()
                    print('OK:', module.__name__, name)
                except AssertionError as ae:
                    print('FAILED:', module.__name__, name, ae)
                    failed = True
                except Exception as e:
                    print('ERROR:', module.__name__, name, e)
                    failed = True

if failed:
    print('SOME TESTS FAILED')
    sys.exit(1)
print('ALL TESTS PASSED')
sys.exit(0)
