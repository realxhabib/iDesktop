"""Check floating cross-monitor dragging without moving real windows or requiring a phone.
Usage: python tools/test_window_drag.py (needs Windows and Node.js).
"""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from idesktop import viewer_layer, winshape  # noqa: E402


class WindowDragTests(unittest.TestCase):
    def api(self, *, pressed=True, target=2, current=1):
        api = Mock()
        api.GetAsyncKeyState.return_value = 0x8000 if pressed else 0
        api.MonitorFromPoint.return_value = target
        api.MonitorFromWindow.return_value = current
        api.SetWindowPos.return_value = True

        def cursor(out):
            out._obj.x, out._obj.y = -1200, 800
            return True

        def monitor(handle, out):
            out._obj.rcWork = winshape.wintypes.RECT(-2560, 550, 0, 2078)
            return True

        api.GetCursorPos.side_effect = cursor
        api.GetMonitorInfoW.side_effect = monitor
        return api

    def test_same_monitor_and_display_gap_do_not_move(self):
        for target in (None, 1):
            with self.subTest(target=target):
                api = self.api(target=target)
                with patch.object(winshape, 'user32', api):
                    self.assertIsNone(winshape._follow_drag(99))
                api.SetWindowPos.assert_not_called()

    def test_release_ignores_late_drag_requests(self):
        api = self.api(pressed=False)
        with patch.object(winshape, 'user32', api):
            self.assertIsNone(winshape._follow_drag(99))
        api.GetCursorPos.assert_not_called()
        api.SetWindowPos.assert_not_called()

    def test_move_to_negative_coordinate_monitor(self):
        api = self.api()
        content = winshape.wintypes.RECT(-2560, 550, 0, 2078)
        with patch.object(winshape, 'user32', api), patch.object(winshape, '_content_rect', return_value=content):
            result = winshape._follow_drag(99)
        api.SetWindowPos.assert_called_once_with(
            99, None, -2560, 550, 2560, 1528, winshape.SWP_NOZORDER | winshape.SWP_NOACTIVATE)
        self.assertEqual([call.args[1] for call in api.ShowWindow.call_args_list],
                         [winshape.SW_RESTORE, winshape.SW_MAXIMIZE])
        self.assertEqual(result['cursor'], [1360, 250])
        self.assertEqual(result['content'], [2560, 1528])

    def test_no_monitor_info_leaves_window_unchanged(self):
        api = self.api()
        api.GetMonitorInfoW.side_effect = None
        api.GetMonitorInfoW.return_value = False
        with patch.object(winshape, 'user32', api):
            self.assertIsNone(winshape._follow_drag(99))
        api.ShowWindow.assert_not_called()

    def test_watchdog_never_replays_drag(self):
        spec = {'maximize': True, 'dpr': 1.5, 'shapes': [], 'drag': [1, 8]}
        with patch.object(winshape, '_watch', object()), patch.object(winshape, '_last_spec', None), \
                patch.object(winshape, '_apply', return_value={'ok': True}):
            winshape.apply_layout(spec)
            self.assertNotIn('drag', winshape._last_spec)
            self.assertEqual(spec['drag'], [1, 8])

    def test_classic_layout_does_not_follow_cursor(self):
        api = self.api()
        api.IsZoomed.return_value = False
        with patch.object(winshape, 'user32', api), patch.object(winshape, 'find_window', return_value=99), \
                patch.object(winshape, '_set_backdrop'), patch.object(winshape, '_follow_drag') as follow:
            self.assertEqual(winshape._apply({'off': True, 'drag': [1, 1]}), {'ok': True})
        follow.assert_not_called()

    def test_frontend_waits_for_dpi_then_preserves_grabbed_point(self):
        # Execute the actual landing/clamping block with a delayed 100% -> 150% viewport update.
        js = viewer_layer.BODY_INJECT
        start = js.index('    if (dragLanding) {')
        end = js.index('    const OX =', start)
        block = js[start:end]
        harness = """
const assert = require('node:assert/strict');
let dpr = 1, innerWidth = 1920, innerHeight = 1080, full = true;
let pw = 400, ph = 800, PAD = 4, CXR = 418, FAB = 42, below = 34;
let posX = 40, posY = 8, dLX = 0, dLY = 0, dragAnchor = [0.25, 0.1];
let dragPointer = null;
let dragLanding = {cursor: [1200, 300], content: [2560, 1528], at: 0};
let now = 100, scheduled = 0;
const performance = {now: () => now};
const requestAnimationFrame = () => scheduled++;
const window = {fitCanvasToViewport() {}};
function land() { BLOCK }
assert.equal(land(), true);
assert.equal(scheduled, 1);
assert.equal(posX, 40);
dpr = 1.5; innerWidth = 2560 / dpr; innerHeight = 1528 / dpr;
land();
assert.equal(dragLanding, null);
assert.equal(posX + PAD + dragAnchor[0] * pw, 800);
assert.equal(posY + dragAnchor[1] * ph, 200);
assert.equal(dLX, 800); assert.equal(dLY, 200);
// A disconnected/small target screen must keep the phone's origin on-screen.
dragLanding = {cursor: [-30, -30], content: [2560, 1528], at: now};
land(); assert.equal(posX, 0); assert.equal(posY, 0);
""".replace('BLOCK', block)
        result = subprocess.run(['node', '-e', harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_pointer_motion_and_release_during_landing_are_preserved(self):
        js = viewer_layer.BODY_INJECT
        landing = js[js.index('    if (dragLanding) {'):js.index('    const OX =')]
        start = js.index('  let dragPointer = null;')
        handlers = js[start:js.index("  extPhone.addEventListener('contextmenu'", start)]
        harness = """
const assert = require('node:assert/strict');
let dpr=1, innerWidth=1920, innerHeight=1080, full=true;
let pw=400,ph=500,PAD=4,CXR=418,FAB=42,below=34;
let posX=40,posY=8,dLX=0,dLY=0,dragAnchor=[0,0];
let dragId=null,dragTick=0,dragSession=0,dragRaf=0,dragLanding=null,dragSavePending=false;
let bigFloat=false,layoutSent='',now=100;
const performance={now:()=>now};
const requestAnimationFrame=()=>1;
const window={screenX:0,screenY:0,devicePixelRatio:1,fitCanvasToViewport(){}};
const listeners={};
const extBezel={},camCtl={};
const extPhone={addEventListener:(name,fn)=>{listeners[name]=fn;},setPointerCapture(){},
                getBoundingClientRect:()=>({left:44,top:8,width:pw,height:ph})};
HANDLERS
function event(x,y,type='pointermove') {
  return {target:extPhone,button:0,pointerId:1,clientX:x,clientY:y,type,preventDefault(){}};
}
function land(){ LANDING }
listeners.pointerdown(event(144,58,'pointerdown'));
// The most recent event is still on the source display: do not reuse its client coordinates.
window.screenX=1920;window.devicePixelRatio=dpr=1.5;
innerWidth=2560/dpr;innerHeight=1528/dpr;
dragLanding={cursor:[900,300],content:[2560,1528],at:now};
land();
assert.equal(posX+PAD+dragAnchor[0]*pw,600);
assert.equal(posY+dragAnchor[1]*ph,200);
// A newer event arrives during the DPI wait and must replace the helper's old snapshot.
dragLanding={cursor:[900,300],content:[2560,1528],at:now};
listeners.pointermove(event(900,300));
land();
assert.equal(posX+PAD+dragAnchor[0]*pw,900);
assert.equal(posY+dragAnchor[1]*ph,300);
// Releasing before the scheduled layout must retain the final pointer, too.
dragLanding={cursor:[900,300],content:[2560,1528],at:now};
listeners.pointerup(event(950,350,'pointerup'));
land();
assert.equal(dragId,null);
assert.equal(posX+PAD+dragAnchor[0]*pw,950);
assert.equal(posY+dragAnchor[1]*ph,350);
assert.equal(dragSavePending,true);
// Release before DPI/viewport updates: retain physical position even if screenX also changes.
for (const [oldDpr,newDpr,oldOrigin,newOrigin] of [[1,1.5,1920,1280],[1.5,1,-1280,-1920]]) {
  window.devicePixelRatio=dpr=oldDpr;window.screenX=oldOrigin;
  innerWidth=1920;innerHeight=1080;
  dragId=1;dragRaf=0;
  dragLanding={cursor:[900,300],content:[2560,1528],at:now};
  assert.equal(land(),true);
  listeners.pointermove(event(1200/oldDpr,400/oldDpr));
  listeners.pointerup(event(1200/oldDpr,400/oldDpr,'pointerup'));
  assert.equal(dragId,null);
  assert.notEqual(dragLanding,null);
  window.devicePixelRatio=dpr=newDpr;window.screenX=newOrigin;
  innerWidth=2560/dpr;innerHeight=1528/dpr;
  land();
  assert.equal(dragLanding,null);
  assert.equal(posX+PAD+dragAnchor[0]*pw,1200/newDpr);
  assert.equal(posY+dragAnchor[1]*ph,400/newDpr);
  assert.equal(dLX,1200/newDpr);
  assert.equal(dLY,400/newDpr);
}
""".replace('HANDLERS', handlers).replace('LANDING', landing)
        result = subprocess.run(['node', '-e', harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
