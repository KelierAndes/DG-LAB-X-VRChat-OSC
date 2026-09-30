# -*- coding: utf-8 -*-
"""OSC bridge: add fire inputs (coyote + ovc)."""
import io

src = io.open('vrc/osc_bridge.py', encoding='utf-8').read()

old = '''        "in_emergency": "DGLabEmergency",'''
new = '''        "in_emergency": "DGLabEmergency",
        "in_fire": "DGLabFire",'''
assert old in src
src = src.replace(old, new)

old = '''        "in_ovc_zap_b": "DGLabOvcInZapB",'''
new = '''        "in_ovc_zap_b": "DGLabOvcInZapB",
        "in_ovc_fire": "DGLabOvcInFire",'''
assert old in src
src = src.replace(old, new)

old = '''        def emergency(_addr, *args):'''
new = '''        def make_fire(family: str = "COYOTE"):
            def handler(_addr, *args):
                if args and _truthy(args[0]):
                    self._spawn(
                        self.commands.fire(slot_id=self.input_target_slot(family))
                    )

            return handler

        def emergency(_addr, *args):'''
assert old in src
src = src.replace(old, new)

old = '''        self._dispatcher.map(f"/avatar/parameters/{cfg['in_emergency']}", emergency)'''
new = '''        self._dispatcher.map(f"/avatar/parameters/{cfg['in_fire']}", make_fire("COYOTE"))
        self._dispatcher.map(f"/avatar/parameters/{cfg['in_emergency']}", emergency)'''
assert old in src
src = src.replace(old, new)

old = '''        self._dispatcher.map(f"/avatar/parameters/{cfg['in_ovc_zap_b']}", make_zap("B", "OVC"))'''
new = '''        self._dispatcher.map(f"/avatar/parameters/{cfg['in_ovc_zap_b']}", make_zap("B", "OVC"))
        self._dispatcher.map(f"/avatar/parameters/{cfg['in_ovc_fire']}", make_fire("OVC"))'''
assert old in src
src = src.replace(old, new)

io.open('vrc/osc_bridge.py', 'w', encoding='utf-8').write(src)
print("osc patch OK")
