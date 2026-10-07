# installer

这里放的是 **Python 官方 Windows 安装包**，`启动.bat` 在发现电脑上没有
Python 时会用它静默安装，装完直接启动程序——用户不需要手动做任何事。

## 当前文件

| 项目 | 值 |
| --- | --- |
| 文件 | `python-3.13.16-amd64.exe` |
| 版本 | Python 3.13.16（Windows 64 位） |
| 来源 | https://www.python.org/ftp/python/3.13.16/python-3.13.16-amd64.exe |
| 大小 | 28.5 MB |
| SHA256 | `FB4F9F5D438B2396DA0086DC70B935C530CB578E37ADC6D354F7AD2037FEE83B` |
| 数字签名 | 有效，签名者 `CN=Python Software Foundation, O=Python Software Foundation` |

## 怎么用

不用手动运行，`启动.bat` 会自动调用，参数是：

```
/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_tcltk=1
       Include_test=0 Include_doc=0 Shortcuts=0 AssociateFiles=0
```

- `InstallAllUsers=0`：只装给当前用户，**不需要管理员权限**
- `PrependPath=1`：加入 PATH
- `Include_tcltk=1`：带上图形界面模块（程序要用来显示窗口）
- `Shortcuts=0 / AssociateFiles=0`：不建快捷方式、不改 `.py` 文件关联

## 换个版本怎么办

把新的 `python-<版本>-amd64.exe` 放进本目录、删掉旧的即可，
`_python.bat` 是按 `python-*-amd64.exe` 通配匹配的，不用改脚本。

换版本后建议核对一下：

```powershell
Get-FileHash .\python-3.13.16-amd64.exe -Algorithm SHA256
Get-AuthenticodeSignature .\python-3.13.16-amd64.exe | Select-Object Status, SignerCertificate
```

签名状态应为 `Valid`、签名者为 Python Software Foundation。

## 为什么是 3.13 而不是 3.12

Python 3.12 分支从 3.12.11 起已经不再发布 Windows 二进制安装包
（只有源码包），而 3.13 仍在持续发布。程序本身要求 Python 3.10+，
3.13 完全兼容。

## 许可

Python 安装包按 PSF License 分发，允许再分发。
