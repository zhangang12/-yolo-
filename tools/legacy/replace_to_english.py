import xml.etree.ElementTree as ET

MAPPING = {
    "车站地面出入口": "station_exit_ground",
    "风亭实体": "vent_group_ground",
    "周边建筑": "surrounding_building",
    "防火间距线": "fire_clearance_line",
    "建筑属性文字": "building_meta",
    "风亭属性文字": "vent_meta",
    "防火间距尺寸数字": "dimension_val",
    "防火分区": "fire_compartment",
    "商铺": "commercial_shop",
    "防火门": "fire_door",
    "楼梯扶梯": "stair_escalator",
    "挡烟垂壁": "draft_curtain",
    "疏散距离线": "evac_distance_line",
    "宽度控制线": "width_dimension_line",
    "房间名称": "room_title",
    "数值及文本": "val_text",
    "区域类型": "zone_type",
    "分类": "class",
    "门扇开启方向": "swing_dir",
    "宽度": "width",
    "文字内容": "text_content"
}

def convert_xml_labels(input_xml_path, output_xml_path):
    """解析XML并将中文的标签或属性名替换为对应的英文"""
    # 解析 XML 文件
    tree = ET.parse(input_xml_path)
    root = tree.getroot()

    # 遍历 XML 中所有的元素节点
    for elem in root.iter():

        # 1. 替换图形元素的 label 属性 (如: label="宽度控制线" -> label="width_dimension_line")
        if "label" in elem.attrib:
            old_label = elem.attrib["label"]
            if old_label in MAPPING:
                elem.attrib["label"] = MAPPING[old_label]

        # 2. 替换 <attribute name="..."> 标签中的 name 属性 (如: name="宽度" -> name="width")
        if "name" in elem.attrib:
            old_name = elem.attrib["name"]
            if old_name in MAPPING:
                elem.attrib["name"] = MAPPING[old_name]

        # 3. 替换配置元数据中的 <name>...</name> 文本节点 (如: <name>防火分区</name> -> <name>fire_compartment</name>)
        if elem.tag == "name" and elem.text in MAPPING:
            elem.text = MAPPING[elem.text]

    # 保存为新文件，并保持原有的 XML 声明格式
    tree.write(output_xml_path, encoding="utf-8", xml_declaration=True)
    print(f"转换完成！纯英文版 XML 已成功保存至: {output_xml_path}")

if __name__ == "__main__":
    # 请确保将 annotations.xml 放在与该脚本同级的目录下，或修改为绝对路径
    input_file = "annotations.xml"
    output_file = "annotations_en.xml"

    try:
        convert_xml_labels(input_file, output_file)
    except FileNotFoundError:
        print(f"错误：未找到输入文件 {input_file}，请检查路径。")
    except Exception as e:
        print(f"运行过程中发生错误: {e}")