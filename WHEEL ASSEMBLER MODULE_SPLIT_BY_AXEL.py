import os
import json
import glob
import shutil
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from PIL import Image


class WheelAssemblerAbsoluteSyncPipeline:
    def __init__(self, main_dir, divide_folders=True, output_dir_name="Scan_Output_Classified"):
        self.main_dir = main_dir
        self.divide_folders = divide_folders
        self.output_dir = os.path.join(main_dir, output_dir_name)

        triggers_search = glob.glob(os.path.join(main_dir, '**', 'triggers_data.csv'), recursive=True)
        if not triggers_search:
            self.triggers_file = os.path.join(main_dir, 'wheel_assembler', 'triggers_data.csv')
        else:
            self.triggers_file = triggers_search[0]

        self.M = 0
        self.N = 0
        self.D34 = 0.0  # Microseconds
        self.D45 = 0.0  # Microseconds
        self.gate_pulses_host = {}
        self.axle_windows = {}
        self.ocr_windows = {}
        self.file_manifest = []

        # Master Synchronization Event Anchors
        self.vehicle_enter_host_usec = 0
        self.ocr_right_anchor_host_usec = 0
        self.laser_master_anchor_host_usec = 0

    def parse_triggers(self):
        """Parses triggers_data.csv, extracts event anchors, and determines vehicle structure M, N, D34, D45."""
        df = pd.read_csv(self.triggers_file)
        usec_col = 'Host_Timestamp_usec' if 'Host_Timestamp_usec' in df.columns else 'Host'

        # 1. Master Vehicle Entry Event Anchor
        veh_enter = df[(df['Event'] == 'VEHICLE_ENTER') & (df['Rising_Edge'] == 1)]
        if not veh_enter.empty:
            self.vehicle_enter_host_usec = veh_enter[usec_col].iloc[0]
        else:
            self.vehicle_enter_host_usec = df[usec_col].min()

        # 2. Camera Anchor: First OUTPUT_OCR_AND_FLASH_RIGHT_TRIGGER Rising Edge
        ocr_anchor = df[(df['Event'] == 'OUTPUT_OCR_AND_FLASH_RIGHT_TRIGGER') & (df['Rising_Edge'] == 1)]
        if not ocr_anchor.empty:
            self.ocr_right_anchor_host_usec = ocr_anchor[usec_col].iloc[0]
        else:
            self.ocr_right_anchor_host_usec = self.vehicle_enter_host_usec

        # 3. Laser Anchor: First TREAD_OUTPUT_LASER_MASTER_ENABLE Rising Edge
        laser_anchor = df[(df['Event'] == 'TREAD_OUTPUT_LASER_MASTER_ENABLE') & (df['Rising_Edge'] == 1)]
        if not laser_anchor.empty:
            self.laser_master_anchor_host_usec = laser_anchor[usec_col].iloc[0]
        else:
            self.laser_master_anchor_host_usec = self.vehicle_enter_host_usec

        # Group Gate Events for Axle Slicing Windows
        gate_events = df[df['Event'].str.startswith('gate', na=False)].sort_values(usec_col)
        for gate_name, group in gate_events.groupby('Event'):
            rises = group[group['Rising_Edge'] == 1].reset_index(drop=True)
            falls = group[group['Rising_Edge'] == 0].reset_index(drop=True)

            pulses = []
            pulse_count = min(len(rises), len(falls))
            for i in range(pulse_count):
                pulses.append({
                    'pulse_idx': i + 1,
                    'rise_host_usec': rises.loc[i, usec_col],
                    'fall_host_usec': falls.loc[i, usec_col]
                })
            self.gate_pulses_host[gate_name] = pulses

        # Calculate M, N, D34, D45
        self.M = max([len(p) for p in self.gate_pulses_host.values()]) if self.gate_pulses_host else 0
        self.D34 = 0.0
        self.D45 = 0.0

        if self.M <= 1:
            self.N = 1
        elif self.M == 2:
            self.N = 2
        else:
            ref_gate = 'gate3' if 'gate3' in self.gate_pulses_host and len(
                self.gate_pulses_host['gate3']) == self.M else 'gate1'
            pulses = self.gate_pulses_host[ref_gate]

            t_rise_M_minus_1 = pulses[self.M - 2]['rise_host_usec']
            t_fall_M_minus_2 = pulses[self.M - 3]['fall_host_usec']
            t_rise_M = pulses[self.M - 1]['rise_host_usec']
            t_fall_M_minus_1 = pulses[self.M - 2]['fall_host_usec']

            self.D34 = float(t_rise_M_minus_1 - t_fall_M_minus_2)
            self.D45 = float(t_rise_M - t_fall_M_minus_1)

            if self.D34 > 3.0 * self.D45:
                self.N = 5
            else:
                self.N = 3 if self.M == 3 else 4

        self._build_axle_windows()

    def _build_axle_windows(self):
        """Builds 0/1 slicing boundaries in Microseconds supporting Gate 3 Rise & Fall Lasers."""
        g1 = self.gate_pulses_host.get('gate1', [])
        g2 = self.gate_pulses_host.get('gate2', [])
        g3 = self.gate_pulses_host.get('gate3', [])

        PAD_LASER_USEC = 300000  # 300 ms padding for Gate 3 Fall laser strobes
        PAD_OCR_USEC = 100000  # 100 ms padding for OCR
        CAM_WINDOW_USEC = 2500000  # 2.5 seconds active window for Tread Mapping

        def get_gate_bounds(pulses, idx):
            if pulses and len(pulses) > idx:
                return pulses[idx]['rise_host_usec'], pulses[idx]['fall_host_usec']
            if g2 and len(g2) > idx:
                return g2[idx]['rise_host_usec'], g2[idx]['fall_host_usec']
            if g1 and len(g1) > idx:
                return g1[idx]['rise_host_usec'], g1[idx]['fall_host_usec']
            return 0, 0

        # OCR Windows (Gate 1 Rise/Fall)
        for axle_idx in range(1, self.N + 1):
            g1_idx = axle_idx - 1
            if len(g1) > g1_idx:
                self.ocr_windows[axle_idx] = [
                    (g1[g1_idx]['rise_host_usec'] - PAD_OCR_USEC, g1[g1_idx]['rise_host_usec'] + PAD_OCR_USEC),
                    (g1[g1_idx]['fall_host_usec'] - PAD_OCR_USEC, g1[g1_idx]['fall_host_usec'] + PAD_OCR_USEC)
                ]

        # Axle Windows (Front Tread: Gate 2 + 2.5s | Rear Tread: Gate 1 + 2.5s)
        if self.N == 1:
            g3_r1, g3_f1 = get_gate_bounds(g3, 0)
            g2_r1, g2_f1 = get_gate_bounds(g2, 0)
            self.axle_windows[1] = {
                'front_laser': (g3_r1 - 100000, g3_f1 + PAD_LASER_USEC),
                'front_camera': (g2_r1, g2_r1 + CAM_WINDOW_USEC)
            }
        elif self.N == 2:
            g3_r1, g3_f1 = get_gate_bounds(g3, 0)
            g2_r1, g2_f1 = get_gate_bounds(g2, 0)
            g3_r2, g3_f2 = get_gate_bounds(g3, 1)
            g2_r2, g2_f2 = get_gate_bounds(g2, 1)

            self.axle_windows[1] = {
                'front_laser': (g3_r1 - 100000, g3_f1 + PAD_LASER_USEC),
                'front_camera': (g2_r1, g2_r1 + CAM_WINDOW_USEC)
            }
            self.axle_windows[2] = {
                'front_laser': (g3_r2 - 100000, g3_f2 + PAD_LASER_USEC),
                'front_camera': (g2_r2, g2_r2 + CAM_WINDOW_USEC)
            }
        elif self.N >= 3:
            g3_r1, g3_f1 = get_gate_bounds(g3, 0)
            g2_r1, _ = get_gate_bounds(g2, 0)
            self.axle_windows[1] = {
                'front_laser': (g3_r1 - 100000, g3_f1 + PAD_LASER_USEC),
                'front_camera': (g2_r1, g2_r1 + CAM_WINDOW_USEC)
            }
            g3_r2, g3_f2 = get_gate_bounds(g3, self.M - 2)
            g2_r2, _ = get_gate_bounds(g2, self.M - 2)
            self.axle_windows[2] = {
                'front_laser': (g3_r2 - 100000, g3_f2 + PAD_LASER_USEC),
                'front_camera': (g2_r2, g2_r2 + CAM_WINDOW_USEC)
            }
            g1_r3, g1_f3 = get_gate_bounds(g1, self.M - 1)
            self.axle_windows[3] = {
                'rear_laser': (g1_r3, g1_f3 + PAD_LASER_USEC),
                'rear_camera': (g1_r3, g1_r3 + CAM_WINDOW_USEC)
            }

    def scan_files(self):
        """Synchronizes all 6 Cameras and 4 Lasers to their respective Event Anchors in microsecond precision."""

        # 1. PROCESS ALL 6 CAMERAS (OCR Left/Right + Tread Front/Rear Left/Right)
        # Anchor: First OUTPUT_OCR_AND_FLASH_RIGHT_TRIGGER Rising Edge
        image_folders = ['tire_wall_l_01', 'tire_wall_r_01', 'tr_front_l_01', 'tr_front_r_01', 'tr_rear_l_01',
                         'tr_rear_r_01']

        # Find global minimum Timestamp_usec in OCR frame 0 to establish camera system zero
        ocr_files = glob.glob(os.path.join(self.main_dir, '**', 'tire_wall_*_01', '*.json'), recursive=True)
        global_cam0_usec = float('inf')
        for ocr_json in ocr_files:
            try:
                with open(ocr_json, 'r') as f:
                    d = json.load(f)
                    u = d.get("Timestamp_usec", 0)
                    if 0 < u < global_cam0_usec:
                        global_cam0_usec = u
            except Exception:
                continue

        if global_cam0_usec == float('inf'):
            global_cam0_usec = 0

        for folder in image_folders:
            folder_paths = glob.glob(os.path.join(self.main_dir, '**', folder), recursive=True)
            for folder_path in folder_paths:
                is_ocr = 'wall' in folder
                is_front = 'front' in folder or is_ocr
                side = 'Left' if '_l_' in folder else 'Right'

                json_files = glob.glob(os.path.join(folder_path, '*.json'))
                for json_file in json_files:
                    try:
                        with open(json_file, 'r') as f:
                            data = json.load(f)
                            frame_usec = data.get("Timestamp_usec", 0)

                            # Delta relative to OCR frame 0
                            delta_cam_usec = frame_usec - global_cam0_usec if global_cam0_usec > 0 else 0
                            calc_host_usec = self.ocr_right_anchor_host_usec + delta_cam_usec

                            tif_file = json_file.replace('.json', '')

                            if is_ocr:
                                media_type = 'OCR'
                                target_axle = self._match_ocr(calc_host_usec)
                            else:
                                media_type = 'Tread'
                                target_axle = self._match_host(calc_host_usec, is_camera=True, is_front=is_front)

                            self._add_to_manifest(tif_file, folder, target_axle, side, media_type, calc_host_usec)
                            self._add_to_manifest(json_file, folder, target_axle, side, media_type, calc_host_usec)
                    except Exception:
                        continue

        # 2. PROCESS ALL 4 LASERS
        # Anchor: First TREAD_OUTPUT_LASER_MASTER_ENABLE Rising Edge
        laser_folders = [
            ('LMI_0_FRONT_LEFT', True, 'Left'),
            ('LMI_1_FRONT_RIGHT', True, 'Right'),
            ('LMI_2_REAR_LEFT', False, 'Left'),
            ('LMI_3_REAR_RIGHT', False, 'Right')
        ]

        # Find global minimum Device_Timestamp_usec in laser frame 0
        all_groove_files = glob.glob(os.path.join(self.main_dir, '**', 'LMI_*', '*_grooves.csv'), recursive=True)
        global_laser0_dev_usec = float('inf')

        for g_file in all_groove_files:
            try:
                df_g = pd.read_csv(g_file)
                col = 'Device_Timestamp_usec' if 'Device_Timestamp_usec' in df_g.columns else (
                    'Host_Timestamp_usec' if 'Host_Timestamp_usec' in df_g.columns else 'Host')
                if not df_g.empty and col in df_g.columns:
                    u_val = int(df_g[col].iloc[0])
                    if 0 < u_val < global_laser0_dev_usec:
                        global_laser0_dev_usec = u_val
            except Exception:
                continue

        if global_laser0_dev_usec == float('inf'):
            global_laser0_dev_usec = 0

        for subfolder, is_front, side in laser_folders:
            folder_paths = glob.glob(os.path.join(self.main_dir, '**', subfolder), recursive=True)
            for folder_path in folder_paths:
                groove_files = glob.glob(os.path.join(folder_path, '*_grooves.csv'))
                for csv_file in groove_files:
                    try:
                        df_csv = pd.read_csv(csv_file)
                        col = 'Device_Timestamp_usec' if 'Device_Timestamp_usec' in df_csv.columns else (
                            'Host_Timestamp_usec' if 'Host_Timestamp_usec' in df_csv.columns else 'Host')
                        if not df_csv.empty and col in df_csv.columns:
                            orig_dev_usec = int(df_csv[col].iloc[0])

                            # Delta relative to Laser frame 0
                            delta_laser_usec = orig_dev_usec - global_laser0_dev_usec if global_laser0_dev_usec > 0 else 0
                            calc_host_usec = self.laser_master_anchor_host_usec + delta_laser_usec

                            target_axle = self._match_host(calc_host_usec, is_camera=False, is_front=is_front)

                            self._add_to_manifest(csv_file, subfolder, target_axle, side, 'Laser', calc_host_usec)

                            profile_csv = csv_file.replace('_grooves.csv', '_profile.csv')
                            if os.path.exists(profile_csv):
                                self._add_to_manifest(profile_csv, subfolder, target_axle, side, 'Laser',
                                                      calc_host_usec)
                    except Exception:
                        continue

    def _add_to_manifest(self, src_file, orig_folder, target_axle, side, media_type, calc_host_usec):
        rel_src_path = os.path.relpath(src_file, self.main_dir)
        filename = os.path.basename(src_file)

        # Calculate delta from VEHICLE_ENTER event
        delta_from_enter_usec = calc_host_usec - self.vehicle_enter_host_usec

        self.file_manifest.append({
            'filename': filename,
            'original_subfolder': orig_folder,
            'original_relative_path': rel_src_path,
            'original_full_path': os.path.abspath(src_file),
            'target_axle': target_axle,
            'side': side,
            'media_type': media_type,
            'calculated_host_timestamp_usec': calc_host_usec,
            'delta_from_vehicle_enter_usec': delta_from_enter_usec
        })

    def _match_host(self, calc_host_usec, is_camera, is_front):
        sensor_key = ('front_' if is_front else 'rear_') + ('camera' if is_camera else 'laser')
        for axle_idx in range(1, self.N + 1):
            win = self.axle_windows.get(axle_idx, {}).get(sensor_key)
            if win and win[0] <= calc_host_usec <= win[1]:
                return f"Axle_{axle_idx}"
        return 'Trash'

    def _match_ocr(self, calc_host_usec):
        for axle_idx, windows in self.ocr_windows.items():
            for win in windows:
                if win[0] <= calc_host_usec <= win[1]:
                    return f"Axle_{axle_idx}"
        return 'Trash'

    def plot_and_show_system_diagram(self):
        """Generates, saves, and displays the complete system pulse diagram."""
        df = pd.read_csv(self.triggers_file)
        usec_col = 'Host_Timestamp_usec' if 'Host_Timestamp_usec' in df.columns else 'Host'
        min_host = df[usec_col].min()

        time_scale = 1e6 if min_host > 1e8 else 1e3
        df['rel_sec'] = (df[usec_col] - min_host) / time_scale
        df_sorted = df.sort_values('rel_sec').reset_index(drop=True)
        max_sec = df_sorted['rel_sec'].max() + 1.0

        g1_rises = df_sorted[(df_sorted['Event'] == 'gate1') & (df_sorted['Rising_Edge'] == 1)]['rel_sec'].tolist()
        g1_falls = df_sorted[(df_sorted['Event'] == 'gate1') & (df_sorted['Rising_Edge'] == 0)]['rel_sec'].tolist()

        g2_rises = df_sorted[(df_sorted['Event'] == 'gate2') & (df_sorted['Rising_Edge'] == 1)]['rel_sec'].tolist()
        g3_rises = df_sorted[(df_sorted['Event'] == 'gate3') & (df_sorted['Rising_Edge'] == 1)]['rel_sec'].tolist()
        g3_falls = df_sorted[(df_sorted['Event'] == 'gate3') & (df_sorted['Rising_Edge'] == 0)]['rel_sec'].tolist()

        CAM_DURATION = 2.5
        laser_pulses_df = df_sorted[df_sorted['Event'] == 'TREAD_OUTPUT_LASER_MASTER_ENABLE'].sort_values('rel_sec')

        def make_pulsed_laser_step(laser_df, win_start, win_end, max_t, pad_end=0.3):
            sub_laser = laser_df[(laser_df['rel_sec'] >= win_start - 0.1) & (laser_df['rel_sec'] <= win_end + pad_end)]
            t_pts, v_pts = [0.0], [0]
            curr = 0
            for _, row in sub_laser.iterrows():
                t, edge = row['rel_sec'], row['Rising_Edge']
                t_pts.extend([t, t])
                v_pts.extend([curr, edge])
                curr = edge
            t_pts.append(max_t)
            v_pts.append(curr)
            return t_pts, v_pts

        def make_step(windows_list, max_t):
            t_pts, v_pts = [0.0], [0]
            for start, end in windows_list:
                t_pts.extend([start, start, end, end])
                v_pts.extend([0, 1, 1, 0])
            t_pts.append(max_t)
            v_pts.append(0)
            return t_pts, v_pts

        fig, axes = plt.subplots(11, 1, figsize=(15, 17), sharex=True)

        hardware_signals = [
            ("Input 10 (Entry)", ["VEHICLE_ENTER"], "navy"),
            ("Gate 1 Signal", ["gate1"], "crimson"),
            ("Gate 2 Signal", ["gate2"], "darkorange"),
            ("Gate 3 Signal", ["gate3"], "goldenrod"),
            ("Input 11 (Exit)", ["VEHICLE_EXIT"], "darkred"),
        ]

        for idx, (label, event_names, color) in enumerate(hardware_signals):
            ax = axes[idx]
            sub_df = df_sorted[df_sorted['Event'].isin(event_names)].sort_values('rel_sec')
            t_pts, v_pts = [0.0], [0]
            curr = 0
            for _, row in sub_df.iterrows():
                t, edge = row['rel_sec'], row['Rising_Edge']
                t_pts.extend([t, t])
                v_pts.extend([curr, edge])
                curr = edge
            t_pts.append(max_sec)
            v_pts.append(curr)

            ax.step(t_pts, v_pts, where='post', color=color, linewidth=2)
            ax.set_ylim(-0.2, 1.3)
            ax.set_yticks([0, 1])
            ax.set_yticklabels(['0', '1'], fontsize=9, fontweight='bold')
            ax.set_ylabel(label, fontsize=8.5, fontweight='bold', rotation=0, labelpad=120, ha='left', color=color)
            ax.grid(True, linestyle='--', alpha=0.4)

        global_actuators = [
            ("Global OCR Right Strobe\n(Output 1)", ["OUTPUT_OCR_AND_FLASH_RIGHT_TRIGGER"], "blue", 5),
            ("Global OCR Left Strobe\n(Output 2)", ["OUTPUT_OCR_AND_FLASH_LEFT_TRIGGER"], "teal", 6),
            ("Global Tread Exposure\n(Output 10)", ["TREAD_OUTPUT_FLASH_CAMERA_PWM"], "green", 7),
            ("Global Laser Enable\n(Output 20)", ["TREAD_OUTPUT_LASER_MASTER_ENABLE"], "purple", 8),
        ]

        for label, event_names, color, ax_idx in global_actuators:
            ax = axes[ax_idx]
            sub_df = df_sorted[df_sorted['Event'].isin(event_names)].sort_values('rel_sec')
            t_pts, v_pts = [0.0], [0]
            curr = 0
            for _, row in sub_df.iterrows():
                t, edge = row['rel_sec'], row['Rising_Edge']
                t_pts.extend([t, t])
                v_pts.extend([curr, edge])
                curr = edge
            t_pts.append(max_sec)
            v_pts.append(curr)

            ax.step(t_pts, v_pts, where='post', color=color, linewidth=2)
            ax.set_ylim(-0.2, 1.3)
            ax.set_yticks([0, 1])
            ax.set_yticklabels(['0', '1'], fontsize=9, fontweight='bold')
            ax.set_ylabel(label, fontsize=8.5, fontweight='bold', rotation=0, labelpad=120, ha='left', color=color)
            ax.grid(True, linestyle='--', alpha=0.4)

        if len(g1_rises) > 0 and len(g2_rises) > 0 and len(g3_rises) > 0:
            ax1_ocr_r, ax1_ocr_f = g1_rises[0], g1_falls[0]
            ax1_tread_start, ax1_tread_end = g2_rises[0], g2_rises[0] + CAM_DURATION
            ax1_laser_start, ax1_laser_end = g3_rises[0], g3_falls[0]

            ax_ax1 = axes[9]
            t_ocr1, v_ocr1 = make_step([(ax1_ocr_r - 0.05, ax1_ocr_r + 0.05), (ax1_ocr_f - 0.05, ax1_ocr_f + 0.05)],
                                       max_sec)
            t_trd1, v_trd1 = make_step([(ax1_tread_start, ax1_tread_end)], max_sec)
            t_lsr1, v_lsr1 = make_pulsed_laser_step(laser_pulses_df, ax1_laser_start, ax1_laser_end, max_sec,
                                                    pad_end=0.3)

            ax_ax1.step(t_ocr1, v_ocr1, where='post', color='blue', linewidth=2, label='OCR (Gate 1 Right & Left)')
            ax_ax1.step(t_trd1, v_trd1, where='post', color='green', linewidth=2, label='Tread Mapping (Gate 2 + 2.5s)')
            ax_ax1.step(t_lsr1, v_lsr1, where='post', color='purple', linewidth=2,
                        label='Laser Groove Pulses (Gate 3 Rise & Fall)')
            ax_ax1.set_ylim(-0.2, 1.3)
            ax_ax1.set_yticks([0, 1])
            ax_ax1.set_yticklabels(['0', '1'], fontsize=9, fontweight='bold')
            ax_ax1.set_ylabel("Axle 1 Sliced Data\n(Combined)", fontsize=8.5, fontweight='bold', rotation=0,
                              labelpad=120, ha='left', color='black')
            ax_ax1.grid(True, linestyle='--', alpha=0.4)
            ax_ax1.legend(loc='upper right', frameon=True, fontsize=8.5)

        if len(g1_rises) > 1 and len(g2_rises) > 1 and len(g3_rises) > 1:
            ax2_ocr_r, ax2_ocr_f = g1_rises[1], g1_falls[1]
            ax2_tread_start, ax2_tread_end = g2_rises[1], g2_rises[1] + CAM_DURATION
            ax2_laser_start, ax2_laser_end = g3_rises[1], g3_falls[1]

            ax_ax2 = axes[10]
            t_ocr2, v_ocr2 = make_step([(ax2_ocr_r - 0.05, ax2_ocr_r + 0.05), (ax2_ocr_f - 0.05, ax2_ocr_f + 0.05)],
                                       max_sec)
            t_trd2, v_trd2 = make_step([(ax2_tread_start, ax2_tread_end)], max_sec)
            t_lsr2, v_lsr2 = make_pulsed_laser_step(laser_pulses_df, ax2_laser_start, ax2_laser_end, max_sec,
                                                    pad_end=0.3)

            ax_ax2.step(t_ocr2, v_ocr2, where='post', color='blue', linewidth=2, label='OCR (Gate 1 Right & Left)')
            ax_ax2.step(t_trd2, v_trd2, where='post', color='green', linewidth=2, label='Tread Mapping (Gate 2 + 2.5s)')
            ax_ax2.step(t_lsr2, v_lsr2, where='post', color='purple', linewidth=2,
                        label='Laser Groove Pulses (Gate 3 Rise & Fall)')
            ax_ax2.set_ylim(-0.2, 1.3)
            ax_ax2.set_yticks([0, 1])
            ax_ax2.set_yticklabels(['0', '1'], fontsize=9, fontweight='bold')
            ax_ax2.set_ylabel("Axle 2 Sliced Data\n(Combined)", fontsize=8.5, fontweight='bold', rotation=0,
                              labelpad=120, ha='left', color='black')
            ax_ax2.grid(True, linestyle='--', alpha=0.4)
            ax_ax2.legend(loc='upper right', frameon=True, fontsize=8.5)

        axes[-1].set_xlabel('Time (Seconds from Vehicle Entry)', fontsize=12, fontweight='bold')
        plt.suptitle('Complete System Event Synchronization Diagram', fontsize=14, fontweight='bold', y=0.985)
        plt.tight_layout()
        plt.subplots_adjust(top=0.95)

        chart_path = os.path.join(self.output_dir, 'system_pulse_diagram.png')
        plt.savefig(chart_path, dpi=300)
        print(f" Pulse timing diagram saved to:\n  -> {chart_path}")
        plt.show()

    def process_and_execute(self):
        os.makedirs(self.output_dir, exist_ok=True)

        # 1. Export Classification Manifest CSV
        manifest_df = pd.DataFrame(self.file_manifest)
        csv_manifest_path = os.path.join(self.output_dir, "scan_classification_manifest.csv")
        manifest_df.to_csv(csv_manifest_path, index=False)
        print("=" * 90)
        print(f" SUCCESS: Classification Manifest CSV written to:\n  -> {csv_manifest_path}")
        print("=" * 90)

        # 2. File Copying and Inversion Step (if divide_folders=True)
        if self.divide_folders:
            copied_count = 0
            inverted_count = 0
            summary_counts = {}

            for item in self.file_manifest:
                src = item['original_full_path']
                if not os.path.exists(src): continue

                target_axle = item['target_axle']
                side = item['side']
                media_type = item['media_type']
                orig_folder = item['original_subfolder']

                if target_axle == 'Trash':
                    dest_dir = os.path.join(self.output_dir, 'Trash', orig_folder)
                else:
                    dest_dir = os.path.join(self.output_dir, target_axle, side, media_type)

                os.makedirs(dest_dir, exist_ok=True)
                dest_file = os.path.join(dest_dir, item['filename'])

                if target_axle != 'Trash' and src.lower().endswith(('.tif', '.tiff')):
                    try:
                        with Image.open(src) as img:
                            img_np = np.array(img)
                            if img_np.dtype == np.uint8:
                                inverted_np = 255 - img_np
                            elif img_np.dtype == np.uint16:
                                inverted_np = 65535 - img_np
                            else:
                                inverted_np = 255 - img_np

                            inverted_img = Image.fromarray(inverted_np)
                            inverted_img.save(dest_file)
                            inverted_count += 1
                    except Exception:
                        shutil.copy2(src, dest_file)
                else:
                    shutil.copy2(src, dest_file)

                copied_count += 1
                key = f"{target_axle} -> {side} -> {media_type}" if target_axle != 'Trash' else f"Trash ({orig_folder})"
                summary_counts[key] = summary_counts.get(key, 0) + 1

            print("=" * 90)
            print(
                f" CLASSIFICATION SUMMARY (M = {self.M} CROSSINGS | N = {self.N} AXLE(S) | D34 = {self.D34:.0f} µs | D45 = {self.D45:.0f} µs)")
            print("=" * 90)
            for dest, count in sorted(summary_counts.items()):
                print(f"  {dest:<50} : {count} files")
            print("=" * 90)
            print(
                f"SUCCESS: Total {copied_count} files processed ({inverted_count} TIF images inverted) into '{self.output_dir}'")
        else:
            print(" Flag 'divide_folders=False' set. Skipping directory creation and file copying.")

        # 3. Generate, Save, and Display Diagram
        self.plot_and_show_system_diagram()


# --- EXECUTION CONTROL ---
target_folder = r"C:\Users\oz.s\Downloads\2026-09-01T08-00-29.000Z_ed2fea0f-569f-43d9-a0cc-b0dc9d4094fa\2026-09-01T08-00-29.000Z_ed2fea0f-569f-43d9-a0cc-b0dc9d4094fa"

pipeline = WheelAssemblerAbsoluteSyncPipeline(target_folder, divide_folders=False)
pipeline.parse_triggers()
pipeline.scan_files()
pipeline.process_and_execute()