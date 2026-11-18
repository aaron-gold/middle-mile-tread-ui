import os
import json
import glob
import shutil
import pandas as pd


class WheelAssemblerComplete:
    def __init__(self, main_dir, output_dir_name="Scan_Output_Classified"):
        self.main_dir = main_dir
        self.output_dir = os.path.join(main_dir, output_dir_name)

        triggers_search = glob.glob(os.path.join(main_dir, '**', 'triggers_data.csv'), recursive=True)
        if not triggers_search:
            self.triggers_file = os.path.join(main_dir, 'wheel_assembler', 'triggers_data.csv')
        else:
            self.triggers_file = triggers_search[0]

        self.M = 0
        self.N = 0
        self.gate_pulses_host = {}
        self.axle_windows = {}
        self.ocr_windows = {}
        self.file_manifest = []

    def parse_triggers(self):
        """Parses triggers_data.csv and establishes slicing windows."""
        df = pd.read_csv(self.triggers_file)
        gate_events = df[df['Event'].str.startswith('gate', na=False)].sort_values('Host')

        for gate_name, group in gate_events.groupby('Event'):
            rises = group[group['Rising_Edge'] == 1].reset_index(drop=True)
            falls = group[group['Rising_Edge'] == 0].reset_index(drop=True)

            pulses = []
            pulse_count = min(len(rises), len(falls))
            for i in range(pulse_count):
                pulses.append({
                    'pulse_idx': i + 1,
                    'rise_host': rises.loc[i, 'Host'],
                    'fall_host': falls.loc[i, 'Host']
                })
            self.gate_pulses_host[gate_name] = pulses

        self.M = max([len(p) for p in self.gate_pulses_host.values()]) if self.gate_pulses_host else 0

        # Calculate N
        if self.M < 2:
            self.N = self.M
        elif self.M == 2:
            self.N = 2
        else:
            ref_gate = 'gate3' if 'gate3' in self.gate_pulses_host and len(
                self.gate_pulses_host['gate3']) == self.M else 'gate1'
            pulses = self.gate_pulses_host[ref_gate]

            t_rise_M_minus_1 = pulses[self.M - 2]['rise_host']
            t_fall_M_minus_2 = pulses[self.M - 3]['fall_host']
            t_rise_M = pulses[self.M - 1]['rise_host']
            t_fall_M_minus_1 = pulses[self.M - 2]['fall_host']

            D34 = t_rise_M_minus_1 - t_fall_M_minus_2
            D45 = t_rise_M - t_fall_M_minus_1

            if D34 > 3 * D45:
                self.N = 5
            else:
                self.N = 3 if self.M == 3 else 4

        self._build_axle_windows()

    def _build_axle_windows(self):
        """Builds slicing boundaries for Laser, Tread, and OCR."""
        g1 = self.gate_pulses_host.get('gate1', [])
        g2 = self.gate_pulses_host.get('gate2', [])
        g3 = self.gate_pulses_host.get('gate3', [])

        PAD_LASER = 200  # 200 ms padding
        PAD_OCR = 100  # 100 ms tolerance around GATE 1 Rise and Fall edges

        # 1. OCR Windows (tire_wall_l_01 & tire_wall_r_01 aligned to GATE 1 edges)
        for axle_idx in range(1, self.N + 1):
            g1_idx = axle_idx - 1
            if len(g1) > g1_idx:
                self.ocr_windows[axle_idx] = [
                    (g1[g1_idx]['rise_host'] - PAD_OCR, g1[g1_idx]['rise_host'] + PAD_OCR),  # Rising edge image
                    (g1[g1_idx]['fall_host'] - PAD_OCR, g1[g1_idx]['fall_host'] + PAD_OCR)  # Falling edge image
                ]

        # 2. Tread & Laser Slicing Windows
        if self.N == 2:
            self.axle_windows[1] = {
                'front_laser': (g3[0]['rise_host'], g3[0]['fall_host'] + PAD_LASER),
                'front_camera': (g2[0]['rise_host'], g3[0]['fall_host'])
            }
            self.axle_windows[2] = {
                'front_laser': (g3[1]['rise_host'], g3[1]['fall_host'] + PAD_LASER),
                'front_camera': (g2[1]['rise_host'], g3[1]['fall_host'])
            }

        elif self.N == 3:
            self.axle_windows[1] = {
                'front_laser': (g3[0]['rise_host'], g3[0]['fall_host'] + PAD_LASER),
                'front_camera': (g2[0]['rise_host'], g3[0]['fall_host'])
            }
            self.axle_windows[2] = {
                'front_laser': (g3[self.M - 2]['rise_host'], g3[self.M - 2]['fall_host'] + PAD_LASER),
                'front_camera': (g2[self.M - 2]['rise_host'], g3[self.M - 2]['fall_host'])
            }
            self.axle_windows[3] = {
                'rear_laser': (g1[self.M - 1]['rise_host'], g1[self.M - 1]['fall_host']),
                'rear_camera': (g1[self.M - 1]['rise_host'], g2[self.M - 1]['fall_host'])
            }

        elif self.N == 4:
            self.axle_windows[1] = {
                'front_laser': (g3[0]['rise_host'], g3[0]['fall_host'] + PAD_LASER),
                'front_camera': (g2[0]['rise_host'], g3[0]['fall_host'])
            }
            self.axle_windows[2] = {
                'front_laser': (g3[self.M - 3]['rise_host'], g3[self.M - 3]['fall_host'] + PAD_LASER),
                'front_camera': (g2[self.M - 3]['rise_host'], g3[self.M - 3]['fall_host'])
            }
            self.axle_windows[3] = {
                'rear_laser': (g1[self.M - 2]['rise_host'], g1[self.M - 2]['fall_host']),
                'rear_camera': (g1[self.M - 2]['rise_host'], g2[self.M - 2]['fall_host'])
            }
            self.axle_windows[4] = {
                'front_laser': (g3[self.M - 1]['rise_host'], g3[self.M - 1]['fall_host'] + PAD_LASER),
                'front_camera': (g2[self.M - 1]['rise_host'], g3[self.M - 1]['fall_host'])
            }

        elif self.N == 5:
            self.axle_windows[1] = {
                'front_laser': (g3[0]['rise_host'], g3[0]['fall_host'] + PAD_LASER),
                'front_camera': (g2[0]['rise_host'], g3[0]['fall_host'])
            }
            self.axle_windows[2] = {
                'front_laser': (g3[self.M - 4]['rise_host'], g3[self.M - 4]['fall_host'] + PAD_LASER),
                'front_camera': (g2[self.M - 4]['rise_host'], g3[self.M - 4]['fall_host'])
            }
            self.axle_windows[3] = {
                'rear_laser': (g1[self.M - 3]['rise_host'], g1[self.M - 3]['fall_host']),
                'rear_camera': (g1[self.M - 3]['rise_host'], g2[self.M - 3]['fall_host'])
            }
            self.axle_windows[4] = {
                'front_laser': (g3[self.M - 2]['rise_host'], g3[self.M - 2]['fall_host'] + PAD_LASER),
                'front_camera': (g2[self.M - 2]['rise_host'], g3[self.M - 2]['fall_host'])
            }
            self.axle_windows[5] = {
                'rear_laser': (g1[self.M - 1]['rise_host'], g1[self.M - 1]['fall_host']),
                'rear_camera': (g1[self.M - 1]['rise_host'], g2[self.M - 1]['fall_host'])
            }

    def scan_files(self):
        # 1. Process Laser Profilers
        gate3_rise1_host = self.gate_pulses_host['gate3'][0]['rise_host']
        laser_folders = [
            ('LMI_0_FRONT_LEFT', True, 'Left'),
            ('LMI_1_FRONT_RIGHT', True, 'Right'),
            ('LMI_2_REAR_LEFT', False, 'Left'),
            ('LMI_3_REAR_RIGHT', False, 'Right')
        ]

        for subfolder, is_front, side in laser_folders:
            folder_paths = glob.glob(os.path.join(self.main_dir, '**', subfolder), recursive=True)
            for folder_path in folder_paths:
                frame_0_csv = os.path.join(folder_path, 'frame_0_grooves.csv')
                base_msec = None
                if os.path.exists(frame_0_csv):
                    try:
                        df_f0 = pd.read_csv(frame_0_csv)
                        if not df_f0.empty and 'timestamp_msec' in df_f0.columns:
                            base_msec = df_f0['timestamp_msec'].iloc[0]
                    except Exception:
                        pass

                for csv_file in glob.glob(os.path.join(folder_path, '*.csv')):
                    try:
                        df_csv = pd.read_csv(csv_file)
                        if not df_csv.empty and 'timestamp_msec' in df_csv.columns:
                            ts_msec = df_csv['timestamp_msec'].iloc[0]
                            calc_host = (gate3_rise1_host + (ts_msec - base_msec)) if base_msec is not None else ts_msec

                            target_axle = self._match_host(calc_host, is_camera=False, is_front=is_front)
                            self.file_manifest.append({
                                'src_file': csv_file, 'target_axle': target_axle,
                                'side': side, 'media_type': 'Laser'
                            })
                    except Exception:
                        continue

        # 2. Process Cameras (Tread & OCR Tire Wall)
        gate1_rise1_host = self.gate_pulses_host['gate1'][0]['rise_host']
        gate2_rise1_host = self.gate_pulses_host['gate2'][0]['rise_host']

        image_folders = ['tr_front_l_01', 'tr_front_r_01', 'tr_rear_l_01', 'tr_rear_r_01', 'tire_wall_l_01',
                         'tire_wall_r_01']

        for folder in image_folders:
            folder_paths = glob.glob(os.path.join(self.main_dir, '**', folder), recursive=True)
            for folder_path in folder_paths:
                is_ocr = 'wall' in folder
                is_front = 'front' in folder or is_ocr
                side = 'Left' if '_l_' in folder else 'Right'

                json_files = glob.glob(os.path.join(folder_path, '*.json'))
                if not json_files: continue

                min_usec = min([json.load(open(jf)).get("Timestamp_usec", float('inf')) for jf in json_files if
                                json.load(open(jf)).get("Timestamp_usec", 0) > 0], default=0)

                base_host = gate1_rise1_host if is_ocr else gate2_rise1_host

                for json_file in json_files:
                    try:
                        with open(json_file, 'r') as f:
                            data = json.load(f)
                            ts_usec = data.get("Timestamp_usec", 0)
                            delta_cam_ms = (ts_usec - min_usec) / 1000.0 if min_usec > 0 else 0.0
                            calc_host = base_host + delta_cam_ms

                            tif_file = json_file.replace('.json', '')

                            if is_ocr:
                                media_type = 'OCR'
                                target_axle = self._match_ocr(calc_host)
                            else:
                                media_type = 'Tread'
                                target_axle = self._match_host(calc_host, is_camera=True, is_front=is_front)

                            self.file_manifest.append({'src_file': tif_file, 'target_axle': target_axle, 'side': side,
                                                       'media_type': media_type})
                            self.file_manifest.append({'src_file': json_file, 'target_axle': target_axle, 'side': side,
                                                       'media_type': media_type})
                    except Exception:
                        continue

    def _match_host(self, calc_host, is_camera, is_front):
        sensor_key = ('front_' if is_front else 'rear_') + ('camera' if is_camera else 'laser')
        for axle_idx in range(1, self.N + 1):
            win = self.axle_windows.get(axle_idx, {}).get(sensor_key)
            if win and win[0] <= calc_host <= win[1]:
                return f"Axle_{axle_idx}"
        return 'Trash'

    def _match_ocr(self, calc_host):
        for axle_idx, windows in self.ocr_windows.items():
            for win in windows:
                if win[0] <= calc_host <= win[1]:
                    return f"Axle_{axle_idx}"
        return 'Trash'

    def execute_copy(self):
        copied_count = 0
        summary_counts = {}

        for item in self.file_manifest:
            src = item['src_file']
            if not os.path.exists(src): continue

            target_axle = item['target_axle']
            side = item['side']
            media_type = item['media_type']

            if target_axle == 'Trash':
                dest_dir = os.path.join(self.output_dir, 'Trash')
            else:
                dest_dir = os.path.join(self.output_dir, target_axle, side, media_type)

            os.makedirs(dest_dir, exist_ok=True)
            dest_file = os.path.join(dest_dir, os.path.basename(src))

            shutil.copy2(src, dest_file)
            copied_count += 1

            key = f"{target_axle} -> {side} -> {media_type}" if target_axle != 'Trash' else 'Trash'
            summary_counts[key] = summary_counts.get(key, 0) + 1

        print("=" * 70)
        print(f" FULL CLASSIFICATION SUMMARY (TREAD + LASER + OCR)")
        print("=" * 70)
        for dest, count in sorted(summary_counts.items()):
            print(f"  {dest:<45} : {count} files")
        print("=" * 70)
        print(f"SUCCESS: Total {copied_count} files copied into '{self.output_dir}'")


# --- EXECUTION ---
target_folder = r"C:\Users\itay.f\Claude\Projects\Middle Mile Qualification\Wheel Assembler\Complete Scans\mm_artemis_2026-08-30T12-30-59.000Z_0dd9772f-fa78-4753-b010-0c9fcd94cd6d"

organizer = WheelAssemblerComplete(target_folder)
organizer.parse_triggers()
organizer.scan_files()
organizer.execute_copy();