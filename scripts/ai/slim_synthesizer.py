"""
Helper script to slim synthesizer.py to <320 lines by:
1. Removing methods 316-1009 (now in mixin files)
2. Adding mixin imports
3. Changing class definition to inherit from both mixins
"""

with open('scripts/ai/synthesizer.py', encoding='utf-8') as f:
    lines = f.readlines()

# Keep lines 1-315 (0-indexed: 0-314) + delegation methods 1010-end (0-indexed: 1009+)
kept = lines[:315] + ['\n'] + lines[1009:]

# Add mixin imports after the existing imports block (after the except block ending around line 79)
# Find insertion point: after last 'except' block close at module level
insert_idx = None
for i, l in enumerate(kept[:85]):
    if l.strip().startswith('logger = logging.getLogger'):
        insert_idx = i
        break

if insert_idx is not None:
    mixin_import = (
        '\ntry:\n'
        '    from .synthesizer_burst import BurstSynthesizerMixin\n'
        '    from .synthesizer_file import FileSynthesizerMixin\n'
        'except (ImportError, ModuleNotFoundError):\n'
        '    try:\n'
        '        from spd_analysis_engine.scripts.ai.synthesizer_burst import BurstSynthesizerMixin\n'
        '        from spd_analysis_engine.scripts.ai.synthesizer_file import FileSynthesizerMixin\n'
        '    except (ImportError, ModuleNotFoundError):\n'
        '        from scripts.ai.synthesizer_burst import BurstSynthesizerMixin\n'
        '        from scripts.ai.synthesizer_file import FileSynthesizerMixin\n'
        '\n'
    )
    kept.insert(insert_idx, mixin_import)
    print(f'Inserted mixin imports at index {insert_idx}')

# Change class definition to inherit from both mixins
for i, l in enumerate(kept):
    if l.strip() == 'class AISynthesizer:':
        kept[i] = 'class AISynthesizer(BurstSynthesizerMixin, FileSynthesizerMixin):\n'
        print(f'Changed class definition at line {i+1}')
        break

with open('scripts/ai/synthesizer.py', 'w', encoding='utf-8') as f:
    f.writelines(kept)
print(f'synthesizer.py trimmed to {len(kept)} lines')
