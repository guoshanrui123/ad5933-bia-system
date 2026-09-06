# AD5933 多频生物阻抗采集与校准系统

本仓库保存毕业论文硬件研究的阶段性工程基线：STM32F411 + AD5933 多频扫频固件、串口采集工具、2R1C 复阻抗校准、人体会话技术质控及医院采集应用源码。验证范围与未完成事项见 `docs/CURRENT_STATUS.md`，并非临床验证产品。

仓库不包含人体原始数据、临床记录、受试者编号、医院 Excel、打包后的 EXE 或历史失败数据。

## 当前固定配置

- MCU：STM32F411RCT6
- 阻抗芯片：AD5933
- 激励配置：Range 4 / PGA ×1
- 测量方式：两电极
- 测量支路：外部固定串联 10 kΩ
- 扫频：5–100 kHz，5 kHz 步进
- 核心人体分析频点：10、15、20 kHz
- 边界探索频点：5、25 kHz
- 2R1C 标准负载拓扑：`R1 ∥ (R2 + C)`，其中 R2 与 C 串联

## 目录

```text
firmware/                   STM32F411 HAL 固件及构建配置
tools/                      采集、校准、质控和医院应用（保留同级依赖）
calibration/reference_loads 匿名台架校准与独立验证原始数据
docs/                       已确认的技术状态和使用说明
```

## 固件构建

需要 ARM GNU Toolchain、CMake 和 Ninja，均加入 PATH。在 Windows PowerShell 中（也可使用参数指定工具路径）：

```powershell
cd firmware
.\BUILD.ps1
```

构建产物写入 `firmware/build/`，不会提交到 Git。

## Python 工具

推荐 Python 3.11–3.13：

```bash
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
```

从仓库根目录运行：

```bash
python tools/verify_baseline.py
python tools/calibrate_ser10k_2r1c.py calibration/reference_loads/SER10K-LOW-VALID03-R1_100K-R2_840R-C_11N.txt
python tools/log_5min_txt.py --help
python tools/prepare_hospital_workspace.py
python tools/hospital_capture_app.py --smoke-test
python tools/hospital_capture_app.py
```

初始化脚本仅创建本地空白工作簿并复制台架参考文件，不包含任何患者数据，不覆盖已有文件。默认目录 `logs/0904-医院校准数据`；可通过环境变量 `AD5933_HOSPITAL_PACKAGE_ROOT` 指定本地输出目录。正式采集前必须核对串口、硬件配置、安全条件和校准参考文件。

依赖历史人体数据的论文绘图、旧负载搜索脚本及旧启动器不纳入首版，原文件仍保留在本地。实时监视器中的旧电阻模型选项不是当前复阻抗校准验证入口。

## 数据与安全边界

- 禁止向仓库提交人体原始TXT、临床Excel、照片、视频或任何可识别受试者的信息。
- 技术QC通过不等于临床有效，也不等于数据位于校准插值域内。
- 外部10 kΩ只属于测量链路的一部分，不能替代医疗电气隔离、漏电流评估、伦理审批和知情同意。
- 任何Range、PGA、串联电阻、连接器、夹具或模拟前端变化都要求重新验证或校准。

## Git协作约定

后续代码和文档先在本地完成、验证并形成变更摘要。只有在项目负责人明确同意后才推送到远程仓库。
