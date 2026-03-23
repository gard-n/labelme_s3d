import json
import sys
import argparse
import re
import os
import numpy as np
from shapely.geometry import Polygon, LineString


def parse_arguments():
    parser = argparse.ArgumentParser(description="Convert S3D files")
    parser.add_argument("--path_in", "-i", type=str, help="Input directory path", required=True)
    parser.add_argument("--path_out", "-o", type=str, help="Output directory path", required=True)
    parser.add_argument("--size", "-m", type=float, default=-1, help="size of plan in qm", required=False)

    return parser.parse_args()


def get_intersection_points(polygon1, polygon2):
    poly1 = Polygon(polygon1).exterior
    poly2 = Polygon(polygon2).exterior

    lines1 = [LineString(poly1.coords[i : i + 2]) for i in range(len(poly1.coords) - 1)]
    lines2 = [LineString(poly2.coords[i : i + 2]) for i in range(len(poly2.coords) - 1)]

    intersections = []
    for line1 in lines1:
        for line2 in lines2:
            inter = line1.intersection(line2)
            if inter.geom_type in ["Point", "MultiPoint"]:
                intersections.extend(list(inter.coords) if inter.geom_type == "MultiPoint" else [list(inter.coords)])

    # print(len(intersections))
    return intersections


def rectangle_to_polygon(points):
    # Extract the coordinates
    x1, y1 = points[0]
    x2, y2 = points[1]

    # Define the four corners of the rectangle (polygon)
    return np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]])  # Bottom-left  # Bottom-right  # Top-right  # Top-left


def extract_opening_flags(flags):
    # Default values for height and paraphet
    height = 0
    height_from_ground = 0

    # Extract the values if the flags are present
    for key, value in flags.items():
        if "paraphet" in key.lower():
            height_from_ground = value
        elif "height" in key.lower():
            height = value

    return [height_from_ground - 1.0, height + height_from_ground - 1.0]


def save_dict_to_json(dictionary, json_path_out):
    folder = os.path.dirname(json_path_out)
    if folder and not os.path.exists(folder):
        os.makedirs(folder, exist_ok=True)

    with open(json_path_out, "w") as json_file:
        json.dump(dictionary, json_file, indent=4)


# Extract flag values
def extract_flag_value(flags, keyword):
    for key in flags:
        if keyword in key:
            return flags[key]
    return None


def main():
    print("Start Conversion")
    args = parse_arguments()

    json_file_path = args.path_in
    json_path_out = args.path_out
    size_m2 = args.size

    print("Input Path:", json_file_path)
    print("Output Path:", json_path_out)
    print("Size in squeare meters:", size_m2)

    with open(json_file_path, "r") as file:
        json_data = json.load(file)

    # print(json.dumps(json_data, indent=4))

    shapes = json_data["shapes"]
    reference_shape = None
    scale_factor = 0
    for shape in shapes:
        if "ref:" in shape["description"]:
            reference_shape = shape
            match = re.search(r"[-+]?\d*\.\d+|\d+", reference_shape["description"])

            if not match:
                print("No reference length.")
                exit()
            reference_length = float(match.group())
            print(f"Extracted reference length: {reference_length}")
            if reference_shape["shape_type"] == "rectangle":
                points = reference_shape["points"]
                scale_factor = max(points[1][0] - points[0][0], points[1][1] - points[0][1]) / reference_length
            else:
                exit()
        if scale_factor < 0:
            exit()

    origin_x = float("inf")
    origin_y = float("inf")
    area = 0
    for shape in shapes:
        if shape["label"] == "room":

            room_points = np.array(shape["points"])
            if shape["shape_type"] == "rectangle":
                room_points = rectangle_to_polygon(room_points)
            area += Polygon(room_points).area
            # Find the smallest x and y coordinates

            min_x = np.min(room_points[:, 0])
            min_y = np.min(room_points[:, 1])

            if min_x < origin_x:
                origin_x = min_x
            if min_y < origin_y:
                origin_y = min_y

    if size_m2 > 0:
        scale_factor = np.sqrt(area / size_m2)
        print("Scale factor specified by total size.")

    # upper left corner
    origin = np.array([origin_x, origin_y])

    _floor_map_json = {}

    _floor_map_json["merger"] = {}
    _floor_map_json["scale_meters_per_coordinate"] = {}

    floor_data = {}
    room_idx = 0
    for shape in shapes:
        if shape["label"] == "room":

            room_points = np.array(shape["points"])
            if shape["shape_type"] == "rectangle":
                room_points = rectangle_to_polygon(room_points)
            elif shape["shape_type"] != "polygon":
                print("unsupproted room type")
                continue

            pano_data = {}
            pano_data["camera_height"] = 1.0
            pano_data["is_primary"] = True
            pano_data["image_path"] = ""
            pano_data["ceiling_height"] = extract_flag_value(shape["flags"], "height")

            layout_raw = {}
            layout_raw["vertices"] = ((room_points - origin) / scale_factor).tolist()
            layout_raw["openings"] = []
            layout_raw["windows"] = []
            layout_raw["doors"] = []

            for opeing_shape in shapes:
                opening_label = opeing_shape["label"]
                if opening_label in ["window", "door", "opening"]:
                    if opeing_shape["shape_type"] == "rectangle":
                        opening_points = rectangle_to_polygon(np.array(opeing_shape["points"]))
                        intersections = np.array(get_intersection_points(room_points, opening_points)).squeeze()
                        if len(intersections) == 2:
                            intersections = ((intersections - origin) / scale_factor).tolist()
                            opeining_flags = extract_opening_flags(opeing_shape["flags"])

                            intersections.append(opeining_flags)
                            # print(intersections)
                            layout_raw[f"{opening_label}s"].extend(intersections)

            pano_data["layout_raw"] = layout_raw
            pano_data["floor_plan_transformation"] = {"rotation": 0.0, "translation": [0, 0], "scale": 1.0}

            floor_data[f"complete_room_{room_idx}"] = {}
            floor_data[f"complete_room_{room_idx}"][f"partial_room_{room_idx}"] = {}
            floor_data[f"complete_room_{room_idx}"][f"partial_room_{room_idx}"][f"pano_{room_idx}"] = pano_data
            room_idx += 1

    # TODO: Adapt scale

    # for shape in shapes:
    #    print(shape["label"])
    _floor_map_json["merger"]["floor_01"] = floor_data
    _floor_map_json["scale_meters_per_coordinate"]["floor_01"] = 1.0
    _floor_map_json["floor_plan_transformation"] = {"rotation": 0.0, "translation": [0, 0], "scale": 1.0}

    save_dict_to_json(_floor_map_json, json_path_out)


if __name__ == "__main__":
    main()
