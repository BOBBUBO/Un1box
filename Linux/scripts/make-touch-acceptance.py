#!/usr/bin/env python3
"""Write private scripts; never starts a guest or modifies its state."""
import argparse
import pathlib
import urllib.parse

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--output-dir", type=pathlib.Path, required=True)
p.add_argument("--start", type=float, default=60, help="guest boot-relative first action time")
for name in ("address", "clear", "go"):
    p.add_argument("--" + name, nargs=2, type=float, metavar=("X", "Y"), required=True,
                   help="normalized portrait coordinates from inspected Safari screenshot")
a = p.parse_args()
if not a.output_dir.is_absolute() or a.start < 0:
    p.error("output directory must be private absolute path; start must be nonnegative")
for xy in (a.address, a.clear, a.go):
    if not all(0 <= value <= 1 for value in xy):
        p.error("coordinates must be normalized 0..1")
a.output_dir.mkdir(parents=True, exist_ok=False)
# No network, SDK, or installed probe. Stock Safari renders a wide colored ruler
# and reports actual DOM contact counts. No viewport tag disables user zoom.
html = ('<body style="font-size:48px"><b id=t>TOUCH 0 MAX 0</b>'
        '<table border=9 width=960 height=500><tr><td bgcolor=red>A 0</td>'
        '<td bgcolor=yellow>B 480</td><td bgcolor=lime>C 960</td></tr></table>'
        '<script>m=0;function f(e){n=e.touches.length;m=Math.max(m,n);'
        's="";for(i=0;i<n;i++)s+=e.touches[i].identifier+",";'
        'document.getElementById("t").innerHTML="TOUCH "+n+" MAX "+m+" IDS "+s}'
        'ontouchstart=ontouchmove=ontouchend=ontouchcancel=f</script>')
url = 'data:text/html,' + urllib.parse.quote(html, safe='')
if len(url.encode()) >= 1024:
    p.error("offline URL exceeds native script argument bound")
(a.output_dir / 'touch-page.html').write_text(html)
(a.output_dir / 'touch-page-url.txt').write_text(url + '\n')

def write(name, actions):
    (a.output_dir / name).write_text('\n'.join(sorted(actions, key=lambda line: float(line.split()[0]))) + '\n')

def tap(t, xy):
    x, y = xy
    return [f'{t:.2f} touch 11 0 {x} {y}', f'{t + .15:.2f} touch 11 2 {x} {y}']

def shot(t, name):
    return f'{t:.2f} screenshot {a.output_dir / (name + ".bmp")}'

s = a.start
write('load-page.txt', [f'{s:.2f} guest frontmost', shot(s + 1, 'safari-before-url')]
      + tap(s + 2, a.address) + tap(s + 4, a.clear)
      + [f'{s + 6:.2f} guest type {url}', shot(s + 9, 'safari-typed-url')]
      + tap(s + 10, a.go)
      + [shot(s + 18, 'safari-loaded-page'), f'{s + 19:.2f} guest frontmost', f'{s + 22:.2f} stop'])
# Run after independently confirming the saved page opens, or append these
# actions to load-page before its Stop with a later --start schedule.
actions = [f'{s:.2f} guest frontmost', shot(s + 1, 'before-pinch')]
actions += [f'{s + 2:.2f} touches 2 17 0 .35 .45 91 0 .65 .45']
for i in range(1, 11):
    left = .35 - i * .023
    right = .65 + i * .023
    actions.append(f'{s + 2 + i * .08:.2f} touches 2 17 1 {left:.3f} .45 91 1 {right:.3f} .45')
actions += [f'{s + 3:.2f} touches 2 17 2 .12 .45 91 2 .88 .45', shot(s + 5, 'after-pinch')]
actions += [shot(s + 2.45, 'during-pinch')]
# Stable IDs cross without being reassigned to a spatial slot. Lift 17 while 91
# remains down and moves; then per-contact cancel 91 and a fresh tap with ID17.
actions += [f'{s + 6:.2f} touches 2 17 0 .25 .6 91 0 .75 .6']
for i in range(1, 11):
    actions.append(f'{s + 6 + i * .08:.2f} touches 2 17 1 {.25 + .05*i:.2f} .6 91 1 {.75 - .05*i:.2f} .6')
actions += [f'{s + 7:.2f} touch 17 2 .75 .6', f'{s + 7.2:.2f} touch 91 1 .2 .55',
            f'{s + 7.4:.2f} touch 91 3 .2 .55', shot(s + 9, 'cross-lift-cancel')]
actions += [shot(s + 6.3, 'cross-before'), shot(s + 6.65, 'cross-after'), shot(s + 7.25, 'independent-lift')]
actions += tap(s + 10, (.5, .45))
# Pause while contacts are held. Production pause cancels them before suspending;
# resume followed by reusing both IDs detects stuck host ownership/rejection.
actions += [f'{s + 12:.2f} touches 2 17 0 .3 .6 91 0 .7 .6', f'{s + 12.2:.2f} pause 1',
            f'{s + 14:.2f} pause 0', f'{s + 15:.2f} touches 2 17 0 .3 .6 91 0 .7 .6',
            f'{s + 15.2:.2f} cancel', f'{s + 16:.2f} touches 2 17 0 .3 .6 91 0 .7 .6',
            f'{s + 16.3:.2f} touches 2 17 2 .3 .6 91 2 .7 .6', shot(s + 19, 'after-pause-cancel'),
            f'{s + 20:.2f} guest frontmost', f'{s + 22:.2f} guest sync', f'{s + 25:.2f} stop']
write('two-contact-lifecycle.txt', actions)
print(a.output_dir)
