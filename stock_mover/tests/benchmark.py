"""Engine speed and memory for one data folder:  python tests/benchmark.py DATA_DIR   (make data: tests/make_scale_data.py 200 out)"""
import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine as E  # noqa: E402
import schema  # noqa: E402

t = time.time()
R = E.run(sys.argv[1], None, schema.load_config())
print(f"{sys.argv[1]}: {time.time() - t:.1f}s, {len(R['trips'])} trips, {len(R['moves'])} lines, "
      f"{(R['checks'].status == 'PASS').sum()}/{len(R['checks'])} rules, peak {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024} MB")
