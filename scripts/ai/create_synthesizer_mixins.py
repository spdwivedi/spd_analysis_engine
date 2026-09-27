"""
Helper script to create synthesizer_burst.py and synthesizer_file.py
from the large synthesizer.py file.
"""
import os

with open('scripts/ai/synthesizer.py', encoding='utf-8') as f:
    lines = f.readlines()

# ---- synthesizer_burst.py ----
burst_header_lines = [
    '"""\n',
    'scripts/ai/synthesizer_burst.py\n',
    '================================\n',
    'BurstSynthesizerMixin: analyze_burst and analyze_burst_overview methods\n',
    'extracted from AISynthesizer to keep individual modules under 500 lines.\n',
    '"""\n',
    'from __future__ import annotations\n',
    '\n',
    'import logging\n',
    'import os\n',
    'import sqlite3\n',
    'from pathlib import Path\n',
    'from typing import Any\n',
    '\n',
    'logger = logging.getLogger(__name__)\n',
    '\n',
    '\n',
    'class BurstSynthesizerMixin:\n',
    '    """\n',
    '    Mixin providing burst-level AI analysis methods for AISynthesizer.\n',
    '    Requires self.engine_root, self.cache_mgr, self.rate_limiter,\n',
    '    self.compute_diff_hash, self.chunk_diff, self._call_gemini, self._call_ollama,\n',
    '    self._fallback_analysis, self.load_config from the base class.\n',
    '    """\n',
    '\n',
]

# analyze_burst: lines 316-535 (0-indexed: 315-534)
# analyze_event = analyze_burst alias: line 535 (0-indexed: 534)
# analyze_burst_overview: lines 768-1006 (0-indexed: 767-1005)
burst_body = lines[315:536] + ['\n'] + lines[767:1007]

burst_content = burst_header_lines + burst_body + ['\n']

with open('scripts/ai/synthesizer_burst.py', 'w', encoding='utf-8') as f:
    f.writelines(burst_content)
print('synthesizer_burst.py written, lines:', len(burst_content))

# ---- synthesizer_file.py ----
file_header_lines = [
    '"""\n',
    'scripts/ai/synthesizer_file.py\n',
    '===============================\n',
    'FileSynthesizerMixin: _fallback_file_analysis and analyze_file methods\n',
    'extracted from AISynthesizer to keep individual modules under 500 lines.\n',
    '"""\n',
    'from __future__ import annotations\n',
    '\n',
    'import logging\n',
    'import os\n',
    'from pathlib import Path\n',
    'from typing import Any\n',
    '\n',
    'try:\n',
    '    from .prompt_builder import build_file_diff_prompt\n',
    'except (ImportError, ModuleNotFoundError):\n',
    '    try:\n',
    '        from spd_analysis_engine.scripts.ai.prompt_builder import build_file_diff_prompt\n',
    '    except (ImportError, ModuleNotFoundError):\n',
    '        from scripts.ai.prompt_builder import build_file_diff_prompt\n',
    '\n',
    'logger = logging.getLogger(__name__)\n',
    '\n',
    '\n',
    'class FileSynthesizerMixin:\n',
    '    """\n',
    '    Mixin providing per-file AI analysis methods for AISynthesizer.\n',
    '    Requires self.cache_mgr, self.rate_limiter, self.compute_diff_hash,\n',
    '    self.chunk_diff, self._call_gemini, self._call_ollama, self.load_config\n',
    '    from the base class.\n',
    '    """\n',
    '\n',
]

# _fallback_file_analysis: lines 537-595 (0-indexed: 536-594)
# analyze_file: lines 596-767 (0-indexed: 595-766)
file_body = lines[536:767]

file_content = file_header_lines + file_body + ['\n']

with open('scripts/ai/synthesizer_file.py', 'w', encoding='utf-8') as f:
    f.writelines(file_content)
print('synthesizer_file.py written, lines:', len(file_content))

print('Done!')
