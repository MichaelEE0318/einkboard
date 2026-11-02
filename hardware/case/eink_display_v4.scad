// =============================================================
//  電子紙桌上看板 外殼 v4.2 —— 電子紙改用黏的、背面只露電源孔
//  底板固定：後方 2 顆 M2.5 自攻（藏在腳墊下）＋ 前方 2 個卡勾
//  2.9" E-ink（87.81×36.7）＋ Raspberry Pi Zero 2W
//
//  結構：上方是一體成形的外罩（圓角頂、側邊倒角，看不到接縫）
//        Pi 鎖在可拆的底板上，散熱孔藏在底板，背面只露 micro USB 電源孔
//  v4：電子紙不用螺絲 —— 前板內側 4 個 L 形定位角，玻璃邊框用雙面膠貼在視窗四周
//  風格：style = "clean"（極簡）| "retro"（復古收音機）| "ribbed"（機殼細紋）
//  列印：外罩「頂面朝下」、底板平放，皆免支撐
// =============================================================

part  = "all";        // "shell" | "plate" | "all" | "assembled" | "eink_test"
style = "clean";

// ---------- 基本 ----------
wall    = 2.4;
top_t   = 2.4;
plate_t = 2.4;
tol     = 0.25;        // 底板唇邊與外罩內壁的間隙
H       = 48;          // 外罩高度（不含底板）
ang     = 20;          // 斜面後仰角度
R_top   = 5;           // 頂面前後兩條邊的圓角
ch_side = 1.2;         // 左右側邊的倒角

// ---------- 2.9" E-ink ----------
eink_size   = [87.81, 36.7];
eink_holes  = [[2.16, 3.5], [85.65, 3.5], [2.16, 33.2], [85.65, 33.2]];
eink_hole_d = 1.8;     // M2 自攻
eink_post_d = 4.2;
tape_t      = 0.6;     // 雙面膠厚度（VHB / 泡棉膠 0.5～1 mm）
glass_t     = 1.2;
eink_gap    = tape_t + glass_t;   // 前板內面 → PCB 正面
guide_t     = 1.2;     // 定位角厚度
guide_leg   = 7;       // 定位角每邊長
guide_h     = 2.6;     // 定位角高度（蓋到玻璃＋一半 PCB，不碰背面零件）
guide_clr   = 0.4;     // 模組四周間隙
pwr_hole    = [12.5, 8.5];   // 電源孔（micro USB 插頭外殼）
eink_pins_right = true;      // Heltec：排針在右端（Waveshare 在左端）
eink_pcb_t  = 1.0;
eink_glass  = [79.0, 36.7];  eink_glass_x0 = 4.36;
eink_win    = [68.5, 30.5];  eink_win_c = [41.3, 18.35];
eink_v0     = 8;
side_margin = 1.5;

// ---------- Pi Zero 2W ----------
pi_len = 65; pi_wid = 30; pcb_t = 1.4;
standoff_h = 5; standoff_d = 6; screw_d = 2.2;
pi_y0_box = 22;       // 決定外殼深度（不要改，外殼就不用重印）
conn_over = 2.0;      // micro USB / mini HDMI 插座突出 PCB 邊緣（實測約 1～1.5 mm，取 2）
conn_clr  = 1.5;      // 插座到後牆內側的間隙（v4.2：0.6 太緊，底板勾住前緣往上翻蓋時插座會刮後牆）
pi_y0 = pi_y0_box + 1 - conn_over - conn_clr;   // v4.2：Pi 比 v4 往前 2.5 mm
hdmi_port = false;     // 要用 mini HDMI 就改 true，會多開一個孔
pi_left   = 7;         // v3：Pi 往左移，讓出右後角給螺絲柱

// ---------- 底板固定（v3）----------
boss_r  = 3;           // 螺絲柱半徑（貼齊兩面牆）
boss_h  = 5;           // 螺絲柱高（M2.5×6 自攻：吃 3.6 mm）
boss_hole = 2.2;
cs_d    = 5.0;         // 底板沉頭孔
nub_w   = 10;          // 前方卡勾寬
nub_dep = 0.8;         // 卡勾伸進前牆的深度

// ---------- 派生 ----------
ta = tan(ang); ca = cos(ang); sa = sin(ang);
inner_w = eink_size[0] + 2 * side_margin;
X = inner_w + 2 * wall;
Y = pi_y0_box + pi_wid + 1 + wall;
x0 = wall;
y_front_in = function(z) wall / ca + z * ta;
eink_u0 = x0 + side_margin;
pcb_x0  = x0 + inner_w - 1 - pi_len - pi_left;
pcb_top = standoff_h + pcb_t;                 // 以底板上表面為 0
port_pwr  = pcb_x0 + (pi_len - 54.0);
port_usb  = pcb_x0 + (pi_len - 41.4);
port_hdmi = pcb_x0 + (pi_len - 12.4);

boss_c = [[x0 + boss_r, Y - wall - boss_r], [X - wall - boss_r, Y - wall - boss_r]];
nub_xs = [X * 0.3, X * 0.7];
$fn = 48;

module panel_frame() { multmatrix([[1,0,0,0],[0,sa,-ca,0],[0,ca,sa,0],[0,0,0,1]]) children(); }
module prof_extrude(xs, len) { translate([xs, 0, 0]) rotate([90,0,90]) linear_extrude(len) children(); }

// 側面輪廓：底邊直角（貼底板），頂部兩角圓角
module profile2d() {
    hull() {
        offset(r = R_top) offset(delta = -R_top) polygon([[0,0],[Y,0],[Y,H],[H*ta,H]]);
        polygon([[0,0],[Y,0],[Y,R_top],[R_top*ta,R_top]]);
    }
}
module outer_body() {
    hull() {
        prof_extrude(0, 0.01) offset(delta = -ch_side) profile2d();
        prof_extrude(ch_side, X - 2*ch_side) profile2d();
        prof_extrude(X - 0.01, 0.01) offset(delta = -ch_side) profile2d();
    }
}
module inner_cavity() {
    prof_extrude(x0, inner_w) polygon([[y_front_in(-1), -1], [Y - wall, -1], [Y - wall, H - top_t],
                                        [y_front_in(H - top_t), H - top_t]]);
}

module eink_cuts() {
    translate([eink_u0 + eink_win_c[0], eink_v0 + eink_win_c[1], 0]) hull() {
        translate([0,0,-wall-1]) linear_extrude(wall*0.6 + 1) square(eink_win, center=true);
        translate([0,0,0.01]) linear_extrude(0.01) offset(r=1) square([eink_win[0]+1.4, eink_win[1]+1.4], center=true);
    }
}
// v4：4 個 L 形定位角（模組放進去就對準視窗），玻璃靠雙面膠固定
module eink_adds() {
    L = eink_size + [2 * guide_clr, 2 * guide_clr];
    translate([eink_u0 - guide_clr, eink_v0 - guide_clr, -wall - guide_h])
        for (cx = [0, 1], cy = [0, 1]) translate([cx * L[0], cy * L[1], 0]) mirror([cx, 0, 0]) mirror([0, cy, 0]) {
            translate([-guide_t, -guide_t, 0]) cube([guide_leg + guide_t, guide_t, guide_h + 0.01]);
            translate([-guide_t, -guide_t, 0]) cube([guide_t, guide_leg + guide_t, guide_h + 0.01]);
        }
}

// ---------- 風格裝飾 ----------
win_c = [eink_u0 + eink_win_c[0], eink_v0 + eink_win_c[1]];
module retro_bezel() {           // 斜面上凸起的圓角螢幕框
    bw = eink_win[0] + 2.4 + 9;  bh = eink_win[1] + 2.4 + 7;
    panel_frame() translate([win_c[0], win_c[1], -0.01]) linear_extrude(1.2)
        difference() {
            offset(r=3) square([bw - 6, bh - 6], center=true);
            offset(r=1) square([eink_win[0]+1.4, eink_win[1]+1.4], center=true);
        }
}
module side_grooves(zs, depth=0.6, w=1.2) {   // 側面水平裝飾溝（左右兩側）
    for (xs = [-1, X - depth]) intersection() {
        prof_extrude(xs, depth + 1) offset(delta = -3) profile2d();
        for (z = zs) translate([xs, -1, z - w/2]) cube([depth + 1, Y + 2, w]);
    }
}
module top_grille() {            // 頂面盲溝（不穿孔），像老收音機的格柵
    gy0 = H*ta + R_top + 4;  gy1 = Y - R_top - 4;
    for (x = [X/2 - 30 : 4 : X/2 + 30])
        translate([x, gy0, H - 0.6]) linear_extrude(1) slot2d(gy1 - gy0, 1.6);
}
module slot2d(len, w) { hull() { circle(d=w, $fn=20); translate([0, len-w]) circle(d=w, $fn=20); } }
module side_ribs(depth=0.6) {    // 側面直向細紋
    for (xs = [-1, X - depth]) intersection() {
        prof_extrude(xs, depth + 1) offset(delta = -2.5) profile2d();
        for (y = [4 : 3 : Y]) translate([xs, y - 0.5, -1]) cube([depth + 1, 1, H + 2]);
    }
}
module back_ribs(depth=0.6) {    // 背面直向細紋（避開接頭窗）
    for (x = [5 : 3 : X - 5]) if (x < port_pwr - 10 || x > port_usb + 10)
        translate([x - 0.5, Y - depth, 4]) cube([1, depth + 1, H - R_top - 6]);
}

// ---------- v3：後角螺絲柱（含 45° 斜撐，外罩倒著印也不會懸空）----------
module boss_body(c) {
    left = c[0] < X/2;
    wx = left ? x0 : X - wall;                 // 側牆內面
    wy = Y - wall;                             // 後牆內面
    zt = boss_h + 2 * boss_r + 1;              // 斜撐收進牆的高度
    hull() {
        translate([c[0], c[1], 0]) cylinder(r = boss_r, h = boss_h);
        translate([wx - 0.01, c[1] - boss_r, 0]) cube([0.02, wy - c[1] + boss_r, zt]);
        translate([c[0] - boss_r, wy - 0.01, 0]) cube([2 * boss_r, 0.02, zt]);
    }
}
module boss_corner_box(c, g = 0) {          // 螺絲柱占用的角落範圍（給唇邊讓位）
    x1 = c[0] < X/2 ? x0 - 1 : c[0] - boss_r - g;
    x2 = c[0] < X/2 ? c[0] + boss_r + g : X;
    translate([x1, c[1] - boss_r - g, -1]) cube([x2 - x1, Y, boss_h + 1]);
}
// ---------- v3：前方卡勾剖面（YZ）：下緣 30° 斜面好印、好卡 ----------
module nub_prof(g = 0) {
    ly0 = y_front_in(3) + tol;
    polygon([[ly0 + 0.5, 0.3 - g], [y_front_in(1.3) - nub_dep - g, 1.3 - g],
             [y_front_in(2.2) - nub_dep - g, 2.2 + g], [ly0 + 0.5, 2.6 + g]]);
}

// =============================================================
module shell() {
    difference() {
        union() {
            outer_body();
            if (style == "retro") retro_bezel();
        }
        inner_cavity();
        panel_frame() eink_cuts();
        // v4：只開 micro USB 電源孔
        translate([port_pwr, Y + 1, pcb_top + 1.3]) rotate([90,0,0]) linear_extrude(wall + 2)
            offset(r=2) square(pwr_hole - [4, 4], center=true);
        if (hdmi_port)
            translate([port_hdmi, Y + 1, pcb_top + 1.6]) rotate([90,0,0]) linear_extrude(wall + 2)
                offset(r=2) square([17 - 4, 10 - 4], center=true);
        if (style == "retro") { side_grooves([H * 0.30, H * 0.30 + 3]); top_grille(); }
        if (style == "ribbed") { side_ribs(); back_ribs(); }
        for (x = nub_xs) prof_extrude(x - nub_w/2 - 0.3, nub_w + 0.6) nub_prof(0.2);   // 卡勾凹槽
    }
    panel_frame() eink_adds();
    difference() {
        for (c = boss_c) boss_body(c);
        for (c = boss_c) translate([c[0], c[1], -1]) cylinder(d = boss_hole, h = boss_h + 1);
    }
}

// =============================================================
//  底板：Pi 螺絲柱、卡入唇邊、隱藏式散熱孔
// =============================================================
module plate() {
    lip_h = 3;
    ly0 = y_front_in(lip_h) + tol;  ly1 = Y - wall - tol;
    difference() {
        union() {
            translate([0, 0, -plate_t]) hull() for (x = [ch_side, X - ch_side], y = [ch_side, Y - ch_side])
                translate([x, y, 0]) cylinder(r = ch_side, h = plate_t, $fn = 24);
            translate([x0 + tol, ly0, -0.01]) difference() {
                cube([inner_w - 2*tol, ly1 - ly0, lip_h]);
                translate([1.6, 1.6, -1]) cube([inner_w - 2*tol - 3.2, ly1 - ly0 - 3.2, lip_h + 2]);
                for (c = boss_c) translate([-x0 - tol, -ly0, 0]) boss_corner_box(c, 0.4);
            }
            for (x = nub_xs) prof_extrude(x - nub_w/2, nub_w) nub_prof();
            for (dx=[3.5, pi_len-3.5], dy=[3.5, pi_wid-3.5])
                translate([pcb_x0+dx, pi_y0+dy, -0.01]) cylinder(d=standoff_d, h=standoff_h);
        }
        for (dx=[3.5, pi_len-3.5], dy=[3.5, pi_wid-3.5])
            translate([pcb_x0+dx, pi_y0+dy, -plate_t + 0.6]) cylinder(d=screw_d, h=standoff_h + plate_t);
        // 散熱孔只開在 Pi 正下方，從外面看不到
        for (x = [pcb_x0 + 12 : 5 : pcb_x0 + pi_len - 12])
            translate([x, pi_y0 + 8, -plate_t - 1]) linear_extrude(plate_t + 2) slot2d(14, 2.2);
        // 螺絲孔＋沉頭（從底部鎖進後角螺絲柱）
        for (c = boss_c) translate([c[0], c[1], -plate_t - 1]) {
            cylinder(d = 2.8, h = plate_t + 2);
            translate([0, 0, 1.6]) cylinder(d1 = cs_d, d2 = 2.8, h = (cs_d - 2.8) / 2);
            cylinder(d = cs_d, h = 1.61);
        }
        // Ø8 矽膠腳墊凹槽：後面兩個剛好蓋住螺絲頭
        for (p = [[9, 9], [X-9, 9], boss_c[0], boss_c[1]]) translate([p[0], p[1], -plate_t - 0.01]) cylinder(d=8.4, h=0.6);
    }
}

// =============================================================
module eink_test() {
    rotate([180,0,0]) difference() {
        union() {
            translate([eink_u0 - 4, eink_v0 - 4, -wall]) cube([eink_size[0] + 8, eink_size[1] + 8, wall]);
            eink_adds();
        }
        eink_cuts();
    }
}
module dummy_pi() {
    translate([pcb_x0, pi_y0, standoff_h]) cube([pi_len, pi_wid, pcb_t]);
    translate([pcb_x0 + 7, pi_y0 + 0.5, pcb_top]) cube([51, 6, 19.5]);
    // 插座（突出 PCB 邊緣 conn_over）：mini HDMI、micro USB ×2
    for (c = [[port_hdmi, 11.2, 3.3], [port_usb, 8, 3.0], [port_pwr, 8, 3.0]])
        translate([c[0] - c[1] / 2, pi_y0 + pi_wid - 5, pcb_top]) cube([c[1], 5 + conn_over, c[2]]);
    translate([pcb_x0 + pi_len - 12, pi_y0 + 9.5, pcb_top]) cube([12 + 2.5, 11.5, 1.5]);   // microSD 卡（右端突出 2.5）
    translate([pcb_x0 - 1, pi_y0 + 6.5, pcb_top]) cube([5.5, 17, 1.3]);                     // CSI 排線座（左端）
    translate([pcb_x0 + 7, pi_y0 + 0.5, standoff_h - 2.6]) cube([51, 5, 2.6]);              // 排針焊腳（PCB 背面）
}
module dummy_eink() {
    panel_frame() translate([eink_u0, eink_v0, -wall - eink_gap]) {
        translate([0, 0, -eink_pcb_t]) cube([eink_size[0], eink_size[1], eink_pcb_t]);
        translate([6, 0, -eink_pcb_t - 4]) cube([eink_size[0] - 6, eink_size[1], 4]);
        translate([eink_glass_x0, 0, 0]) cube([eink_glass[0], eink_glass[1], 1.2]);
        hx = eink_pins_right ? eink_size[0] - 4.5 : 0.5;
        translate([hx + 0.6, 9, -eink_pcb_t - 8.6 - 14]) cube([2.8, 19, 8.6 + 14]);   // 排針＋直插杜邦（2.54 mm 膠座）
    }
}
// 頂面朝下：要用「旋轉」翻面，不能用 mirror（mirror 會印出左右相反的外殼，電源孔跑到另一邊）
module shell_print() { translate([X, 0, H]) rotate([0, 180, 0]) shell(); }
module plate_print() { translate([0, 0, plate_t]) plate(); }

if (part == "shell") shell_print();
if (part == "plate") plate_print();
if (part == "all") { shell_print(); translate([0, Y + 10, 0]) plate_print(); }
if (part == "assembled") { shell(); color("Silver") plate(); }
if (part == "eink_test") eink_test();
