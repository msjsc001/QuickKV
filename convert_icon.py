from PIL import Image
import os

input_path = "icon.png"
output_path = "icon.ico"

if os.path.exists(input_path):
    try:
        img = Image.open(input_path)
        img.save(output_path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        print(f"成功将 {input_path} 转换为 {output_path}")
    except Exception as e:
        print(f"转换失败: {e}")
else:
    print(f"错误: 文件 {input_path} 不存在。")