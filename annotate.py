from pathlib import Path

replacements = {
    'usqr5f2d': [('secularización', 'secularización (секуляризация)')],
    'haaosvr2': [('secularización', 'secularización (секуляризация)')],
    'wzqw2rtj': [('Pretérito imperativo.', 'Pretérito imperativo (прошедшее повелительное наклонение).')],
    '7g66ff3w': [('mover influencias', 'mover influencias (использовать связи)')],
    'x5ohwhm4': [('trámite', 'trámite (процедура)')],
    'msm7utj5': [('mangoneando', 'mangoneando (манипулируя)')],
    '6smfsk7p': [('asaltando', 'asaltando (штурмуя)')],
    'ulez54bn': [('intimidad', 'intimidad (личное пространство)')],
    '6epkctsf': [('rendir cuentas', 'rendir cuentas (отчитываться)')],
    'xqjk5chp': [('canalla.', 'canalla (негодяй).')],
    'g6k3ihgy': [('apología', 'apología (оправдание)')],
    'gwg5q2om': [('Ándate con ojo.', 'Ándate con ojo (будь осторожен).')],
    'du3aydgw': [('No se andan con chiquitas', 'No se andan con chiquitas (не церемонятся)')],
    'vj2gqqk2': [('pasotas', 'pasotas (равнодушные люди)')],
    'mr65wfxn': [('excentricidad', 'excentricidad (эксцентричность)'), ('camufla', 'camufla (маскируется)')],
    'kywbhwrj': [('delincuencia', 'delincuencia (преступность)')],
    'dnfo7a46': [('mermada', 'mermada (ослабленная)')],
    '4bslxuwb': [('chocheaba.', 'chocheaba (впадал в старческое слабоумие).')],
    'qj7he2cy': [('acostumbre.', 'acostumbre (привыкнет).')],
    'fa7llkmi': [('compensar', 'compensar (компенсировать)')],
    'bjhn5siu': [('pinche.', 'pinche (помощник на кухне).')],
    'p4qqeqkt': [('malcríes,', 'malcríes (избалуй),')],
    'hazolyuy': [('revelar', 'revelar (раскрывать)')],
    'hafxudxe': [('liarla', 'liarla (устроить скандал)')],
    '7amz36ej': [('encaprichando', 'encaprichando (увлекаешься)')],
    'xurvn6i3': [('guardería', 'guardería (детский сад)')],
    '2greohsw': [('guardería', 'guardería (детский сад)')],
    '6uwbc5vb': [('funcionarías', 'funcionarías (служащие)')],
}

for line in Path('input.tsv').read_text().splitlines():
    if not line:
        continue
    cols = line.split('\t')
    id_ = cols[0]
    text = cols[1] if len(cols) > 1 else ''
    echo = cols[-1] if len(cols) > 2 else ''
    if id_ in replacements:
        for old, new in replacements[id_]:
            if old in text:
                text = text.replace(old, new, 1)
    print(f"{id_}\t{text}\t{echo}")
