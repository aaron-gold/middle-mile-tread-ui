def generate_updated_vehicle_enter_diagram(self):
    """Generates Figure 1 with strict Input 10 = VEHICLE_ENTER signal."""
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

    def make_pulsed_laser_step_fixed(laser_df, win_start, win_end, max_t, pad_end=0.3):
        sub_laser = laser_df[(laser_df['rel_sec'] >= win_start - 0.1) & (laser_df['rel_sec'] <= win_end + pad_end)]
        t_pts = [0.0]
        v_pts = [0]
        curr = 0
        for _, row in sub_laser.iterrows():
            t = row['rel_sec']
            edge = row['Rising_Edge']
            t_pts.extend([t, t])
            v_pts.extend([curr, edge])
            curr = edge
        t_pts.append(max_t)
        v_pts.append(curr)
        return t_pts, v_pts

    def make_step(windows_list, max_t):
        t_pts = [0.0]
        v_pts = [0]
        for start, end in windows_list:
            t_pts.extend([start, start, end, end])
            v_pts.extend([0, 1, 1, 0])
        t_pts.append(max_t)
        v_pts.append(0)
        return t_pts, v_pts

    fig, axes = plt.subplots(11, 1, figsize=(15, 17), sharex=True)

    # Strictly VEHICLE_ENTER for Input 10 and VEHICLE_EXIT for Input 11
    hardware_signals = [
        ("Input 10 (Entry)", ["VEHICLE_ENTER"], "navy"),
        ("Gate 1 Signal", ["gate1"], "crimson"),
        ("Gate 2 Signal", ["gate2"], "darkorange"),
        ("Gate 3 Signal", ["gate3"], "goldenrod"),
        ("Input 11 (Exit)", ["VEHICLE_EXIT"], "darkred"),
    ]

    # 1. Hardware Gate Signals
    for idx, (label, event_names, color) in enumerate(hardware_signals):
        ax = axes[idx]
        sub_df = df_sorted[df_sorted['Event'].isin(event_names)].sort_values('rel_sec')
        t_pts = [0.0]
        v_pts = [0]
        curr = 0
        for _, row in sub_df.iterrows():
            t = row['rel_sec']
            edge = row['Rising_Edge']
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

    # 2. Global Actuators (Separated OCR Right & OCR Left)
    global_actuators = [
        ("Global OCR Right Strobe\n(Output 1)", ["OUTPUT_OCR_AND_FLASH_RIGHT_TRIGGER"], "blue", 5),
        ("Global OCR Left Strobe\n(Output 2)", ["OUTPUT_OCR_AND_FLASH_LEFT_TRIGGER"], "teal", 6),
        ("Global Tread Exposure\n(Output 10)", ["TREAD_OUTPUT_FLASH_CAMERA_PWM"], "green", 7),
        ("Global Laser Enable\n(Output 20)", ["TREAD_OUTPUT_LASER_MASTER_ENABLE"], "purple", 8),
    ]

    for label, event_names, color, ax_idx in global_actuators:
        ax = axes[ax_idx]
        sub_df = df_sorted[df_sorted['Event'].isin(event_names)].sort_values('rel_sec')
        t_pts = [0.0]
        v_pts = [0]
        curr = 0
        for _, row in sub_df.iterrows():
            t = row['rel_sec']
            edge = row['Rising_Edge']
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

    # 3. Pulsed Axle Tracks
    if len(g1_rises) > 0 and len(g2_rises) > 0 and len(g3_rises) > 0:
        ax1_ocr_r, ax1_ocr_f = g1_rises[0], g1_falls[0]
        ax1_tread_start, ax1_tread_end = g2_rises[0], g2_rises[0] + CAM_DURATION
        ax1_laser_start, ax1_laser_end = g3_rises[0], g3_falls[0]

        ax_ax1 = axes[9]
        t_ocr1, v_ocr1 = make_step([(ax1_ocr_r - 0.05, ax1_ocr_r + 0.05), (ax1_ocr_f - 0.05, ax1_ocr_f + 0.05)],
                                   max_sec)
        t_trd1, v_trd1 = make_step([(ax1_tread_start, ax1_tread_end)], max_sec)
        t_lsr1, v_lsr1 = make_pulsed_laser_step_fixed(laser_pulses_df, ax1_laser_start, ax1_laser_end, max_sec,
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
        t_lsr2, v_lsr2 = make_pulsed_laser_step_fixed(laser_pulses_df, ax2_laser_start, ax2_laser_end, max_sec,
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
    plt.suptitle('Complete System Diagram: Updated Input 10 Strictly Vehicle Enter', fontsize=14, fontweight='bold',
                 y=0.985)
    plt.tight_layout()
    plt.subplots_adjust(top=0.95)

    chart_path = os.path.join(self.output_dir, 'updated_vehicle_enter_diagram.png')
    plt.savefig(chart_path, dpi=300)
    plt.close()
    print(f" Saved updated diagram:\n  -> {chart_path}")