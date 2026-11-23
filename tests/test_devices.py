import sys
import unittest
from collections import namedtuple
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor" / "voice-changer" / "server"))
from vt import devices as dv  # noqa: E402

D = namedtuple("D", "index name hostAPI")
W = "Windows WASAPI"

CABLE_OUT = D(22, "CABLE Output (VB-Audio Virtual Cable)", W)
MIC = D(23, "Microphone (Realtek(R) Audio)", W)
SPK = D(19, "Speakers (Realtek(R) Audio)", W)
CABLE_IN = D(21, "CABLE Input (VB-Audio Virtual Cable)", W)
HEADSET = D(30, "Speakers (H USB Audio)", W)


class ResolveTest(unittest.TestCase):
    def run_resolve(self, inputs, outputs, prefs, default_out):
        with mock.patch.object(dv, "_wasapi_default_output", return_value=default_out):
            return dv.resolve(inputs, outputs, prefs)

    def test_renumbered_devices_are_found_by_name(self):
        prefs = {"input": dv.DevRef(CABLE_OUT.name, W), "output": dv.DevRef(HEADSET.name, W)}
        r = self.run_resolve([MIC, CABLE_OUT], [SPK, CABLE_IN, HEADSET], prefs, default_out=SPK.index)
        self.assertEqual((r.input_id, r.output_id), (22, 30))
        self.assertEqual(r.warnings, [])

    def test_unplugged_headset_falls_back_to_default_with_warning(self):
        prefs = {"input": dv.DevRef(CABLE_OUT.name, W), "output": dv.DevRef(HEADSET.name, W)}
        r = self.run_resolve([MIC, CABLE_OUT], [SPK, CABLE_IN], prefs, default_out=SPK.index)
        self.assertEqual(r.output_id, SPK.index)
        self.assertTrue(any("H USB Audio" in w for w in r.warnings))

    def test_never_falls_back_to_cable_as_output(self):
        r = self.run_resolve([CABLE_OUT], [CABLE_IN], {}, default_out=CABLE_IN.index)
        self.assertIsNone(r.output_id)

    def test_default_is_cable_picks_physical_speaker_over_virtual(self):
        todesk = D(20, "Speakers (ToDesk Virtual Audio)", W)
        prefs = {"input": dv.DevRef(CABLE_OUT.name, W), "output": dv.DevRef(HEADSET.name, W)}
        r = self.run_resolve([CABLE_OUT], [todesk, CABLE_IN, SPK], prefs, default_out=CABLE_IN.index)
        self.assertEqual(r.output_id, SPK.index)
        self.assertTrue(r.warnings)

    def test_first_run_picks_cable_and_default(self):
        r = self.run_resolve([MIC, CABLE_OUT], [SPK, CABLE_IN], {}, default_out=SPK.index)
        self.assertEqual((r.input_id, r.output_id), (CABLE_OUT.index, SPK.index))
        self.assertEqual(r.warnings, [])

    def test_missing_cable_gives_no_input(self):
        r = self.run_resolve([MIC], [SPK], {}, default_out=SPK.index)
        self.assertIsNone(r.input_id)

    def test_follow_default_uses_windows_default_without_warning(self):
        prefs = {"input": dv.DevRef(CABLE_OUT.name, W), "output": dv.DevRef(dv.FOLLOW_DEFAULT, "")}
        r = self.run_resolve([CABLE_OUT], [SPK, HEADSET, CABLE_IN], prefs, default_out=HEADSET.index)
        self.assertEqual((r.output_id, r.warnings), (HEADSET.index, []))

    def test_follow_default_skips_cable_default(self):
        prefs = {"input": dv.DevRef(CABLE_OUT.name, W), "output": dv.DevRef(dv.FOLLOW_DEFAULT, "")}
        r = self.run_resolve([CABLE_OUT], [SPK, CABLE_IN], prefs, default_out=CABLE_IN.index)
        self.assertEqual(r.output_id, SPK.index)

    def test_same_name_other_host_api_is_not_confused(self):
        mme_cable = D(2, "CABLE Output (VB-Audio Virtual Cable)", "MME")
        prefs = {"input": dv.DevRef(CABLE_OUT.name, W), "output": dv.DevRef(SPK.name, W)}
        r = self.run_resolve([mme_cable, CABLE_OUT], [SPK], prefs, default_out=SPK.index)
        self.assertEqual(r.input_id, CABLE_OUT.index)


if __name__ == "__main__":
    unittest.main()
