# Cellium Agent 快速安装

## 一键运行 Cellium Agent

### Windows PowerShell

```powershell
Invoke-WebRequest -Uri https://github.com/Cellium-Project/Cellium-Agent/releases/latest/download/Cellium-Agent-Windows.zip -OutFile Cellium-Agent.zip; Expand-Archive -Path Cellium-Agent.zip -DestinationPath . -Force; cd Cellium-Agent-Windows; .\CelliumAgent.exe
```

### Windows CMD

```cmd
curl -LO https://github.com/Cellium-Project/Cellium-Agent/releases/latest/download/Cellium-Agent-Windows.zip && powershell -Command "Expand-Archive -Path 'Cellium-Agent-Windows.zip' -DestinationPath '.'" && cd Cellium-Agent-Windows && CelliumAgent.exe
```

### Linux x64

```bash
curl -LO https://github.com/Cellium-Project/Cellium-Agent/releases/latest/download/Cellium-Agent-Linux.tar.gz && tar -xzf Cellium-Agent-Linux.tar.gz && cd Cellium-Agent-Linux && ./start-cellium.sh
```

### Linux ARM64

```bash
curl -LO https://github.com/Cellium-Project/Cellium-Agent/releases/latest/download/Cellium-Agent-Linux-ARM64.tar.gz && tar -xzf Cellium-Agent-Linux-ARM64.tar.gz && cd Cellium-Agent-Linux-ARM64 && ./start-cellium.sh
```

### macOS

```bash
curl -LO https://github.com/Cellium-Project/Cellium-Agent/releases/latest/download/Cellium-Agent-macOS.tar.gz && tar -xzf Cellium-Agent-macOS.tar.gz && cd Cellium-Agent-macOS && ./start-cellium.sh
```

---

## 注意事项

### Linux ARM64

如需使用网页搜索功能，请安装 Chromium：

```bash
sudo apt install chromium-browser
```

### 手动下载

从 [Releases](https://github.com/Cellium-Project/Cellium-Agent/releases) 页面下载对应平台的压缩包：

| 平台 | 文件 | 解压后目录 |
|------|------|-----------|
| Windows | Cellium-Agent-Windows.zip | Cellium-Agent-Windows |
| Linux x64 | Cellium-Agent-Linux.tar.gz | Cellium-Agent-Linux |
| Linux ARM64 | Cellium-Agent-Linux-ARM64.tar.gz | Cellium-Agent-Linux-ARM64 |
| macOS | Cellium-Agent-macOS.tar.gz | Cellium-Agent-macOS |

解压后运行：
- Windows: 双击 `CelliumAgent.exe`
- Linux/macOS: `./start-cellium.sh`
