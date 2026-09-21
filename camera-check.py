"""Check camera capture without saving or transmitting frames."""
import json
import subprocess
import sys

if len(sys.argv) > 1:
    import cv2
    backend = sys.argv[1]
    cap = cv2.VideoCapture(0, getattr(cv2, backend))
    opened = cap.isOpened()
    captured = False
    shape = None
    for _ in range(10 if opened else 0):
        ok, frame = cap.read()
        if ok:
            captured, shape = True, list(frame.shape)
            break
    print(json.dumps({'version': cv2.__version__, 'requested_backend': backend,
                      'actual_backend': cap.getBackendName() if opened else None,
                      'opened': opened, 'frame': captured, 'shape': shape}), flush=True)
    cap.release()
else:
    for backend in ['CAP_ANY', 'CAP_MSMF', 'CAP_DSHOW']:
        try:
            result = subprocess.run([sys.executable, __file__, backend], capture_output=True, text=True, timeout=30)
            print(result.stdout.strip(), flush=True)
            if result.stderr:
                print(result.stderr[-2000:], flush=True)
        except subprocess.TimeoutExpired:
            print(json.dumps({'backend': backend, 'error': 'timeout after 30 seconds'}), flush=True)
