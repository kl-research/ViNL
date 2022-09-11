# SPDX-FileCopyrightText: Copyright (c) 2021 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this
# list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
# this list of conditions and the following disclaimer in the documentation
# and/or other materials provided with the distribution.
#
# 3. Neither the name of the copyright holder nor the names of its
# contributors may be used to endorse or promote products derived from
# this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
# Copyright (c) 2021 ETH Zurich, Nikita Rudin
import os
from itertools import permutations

import imageio
import numpy as np
from numpy.random import choice

from isaacgym import terrain_utils
from legged_gym.envs.base.legged_robot_config import LeggedRobotCfg


def to_shape(a, shape):
    y_, x_ = shape
    y, x = a.shape
    y_pad = y_ - y
    x_pad = x_ - x
    return np.pad(
        a,
        ((y_pad // 2, y_pad // 2 + y_pad % 2), (x_pad // 2, x_pad // 2 + x_pad % 2)),
        mode="constant",
    )


class Terrain:
    def __init__(self, cfg: LeggedRobotCfg.terrain, num_robots) -> None:

        self.cfg = cfg
        self.num_robots = num_robots
        self.type = cfg.mesh_type
        if self.type in ["none", "plane"]:
            return
        self.env_length = cfg.terrain_length
        self.env_width = cfg.terrain_width
        self.proportions = [
            np.sum(cfg.terrain_proportions[: i + 1])
            for i in range(len(cfg.terrain_proportions))
        ]

        self.cfg.num_sub_terrains = cfg.num_rows * cfg.num_cols
        self.env_origins = np.zeros((cfg.num_rows, cfg.num_cols, 3))

        self.width_per_env_pixels = int(self.env_width / cfg.horizontal_scale)
        self.length_per_env_pixels = int(self.env_length / cfg.horizontal_scale)

        self.border = int(cfg.border_size / self.cfg.horizontal_scale)
        print("BORDER: ", self.border, cfg.border_size)
        self.tot_cols = int(cfg.num_cols * self.width_per_env_pixels) + 2 * self.border
        self.tot_rows = int(cfg.num_rows * self.length_per_env_pixels) + 2 * self.border

        self.height_field_raw = np.zeros((self.tot_rows, self.tot_cols), dtype=np.int16)
        if cfg.map_path:
            im = np.array(imageio.imread(cfg.map_path))
            im = im[:, :, 3]
            scaled_im = im.repeat(3, axis=0).repeat(3, axis=1)
            self.height_field_raw = to_shape(scaled_im, (900, 900))

        elif cfg.curriculum:
            self.curiculum()
        elif cfg.selected:
            self.selected_terrain()
        else:
            self.randomized_terrain()

        self.heightsamples = self.height_field_raw
        print(f"{self.heightsamples.shape=}")
        if self.type == "trimesh":
            if cfg.map_path:
                hscale, vscale = 0.4, 4
            else:
                hscale, vscale = 1, 1
            (
                self.vertices,
                self.triangles,
            ) = terrain_utils.convert_heightfield_to_trimesh(
                self.height_field_raw,
                self.cfg.horizontal_scale * hscale,
                self.cfg.vertical_scale * vscale,
                self.cfg.slope_treshold,
            )
            if cfg.map_path:
                # Add small blocks on the ground
                self.add_blocks()

    def randomized_terrain(self):
        for k in range(self.cfg.num_sub_terrains):
            # Env coordinates in the world
            (i, j) = np.unravel_index(k, (self.cfg.num_rows, self.cfg.num_cols))

            choice = np.random.uniform(0, 1)
            difficulty = np.random.choice([0.5, 0.75, 0.9])
            terrain = self.make_terrain(choice, difficulty)
            self.add_terrain_to_map(terrain, i, j)

    def curiculum(self):
        num_cols = (
            self.cfg.tot_cols if hasattr(self.cfg, "tot_cols") else self.cfg.num_cols
        )
        num_rows = (
            self.cfg.tot_rows if hasattr(self.cfg, "tot_rows") else self.cfg.num_rows
        )
        for j in range(num_cols):
            for i in range(num_rows):
                difficulty = i / self.cfg.num_rows
                choice = j / self.cfg.num_cols + 0.001

                terrain = self.make_terrain(choice, difficulty)
                self.add_terrain_to_map(terrain, i, j)

    def selected_terrain(self):
        terrain_type = self.cfg.terrain_kwargs.pop("type")
        for k in range(self.cfg.num_sub_terrains):
            # Env coordinates in the world
            (i, j) = np.unravel_index(k, (self.cfg.num_rows, self.cfg.num_cols))

            terrain = terrain_utils.SubTerrain(
                "terrain",
                width=self.width_per_env_pixels,
                length=self.width_per_env_pixels,
                vertical_scale=self.vertical_scale,
                horizontal_scale=self.horizontal_scale,
            )

            eval(terrain_type)(terrain, **self.cfg.terrain_kwargs.terrain_kwargs)
            self.add_terrain_to_map(terrain, i, j)

    def make_terrain(self, choice, difficulty):
        terrain = terrain_utils.SubTerrain(
            "terrain",
            width=self.width_per_env_pixels,
            length=self.width_per_env_pixels,
            vertical_scale=self.cfg.vertical_scale,
            horizontal_scale=self.cfg.horizontal_scale,
        )
        slope = difficulty * 0.4
        step_height = 0.05 + 0.18 * difficulty
        discrete_obstacles_height = 0.05 + difficulty * 0.2
        stepping_stones_size = 1.5 * (1.05 - difficulty)
        stone_distance = 0.05 if difficulty == 0 else 0.1
        gap_size = 1.0 * difficulty
        pit_depth = 1.0 * difficulty
        if choice < self.proportions[0]:
            if choice < self.proportions[0] / 2:
                slope *= -1
            terrain_utils.pyramid_sloped_terrain(
                terrain, slope=slope, platform_size=3.0
            )
        elif choice < self.proportions[1]:
            terrain_utils.pyramid_sloped_terrain(
                terrain, slope=slope, platform_size=3.0
            )
            terrain_utils.random_uniform_terrain(
                terrain,
                min_height=-0.05,
                max_height=0.05,
                step=0.005,
                downsampled_scale=0.2,
            )
        elif choice < self.proportions[3]:
            if choice < self.proportions[2]:
                step_height *= -1
            terrain_utils.pyramid_stairs_terrain(
                terrain, step_width=0.31, step_height=step_height, platform_size=3.0
            )
        elif choice < self.proportions[4]:
            num_rectangles = 20
            rectangle_min_size = 1.0
            rectangle_max_size = 2.0
            terrain_utils.discrete_obstacles_terrain(
                terrain,
                discrete_obstacles_height,
                rectangle_min_size,
                rectangle_max_size,
                num_rectangles,
                platform_size=3.0,
            )
        elif choice < self.proportions[5]:
            num_rectangles = int(200 * difficulty)
            # num_rectangles = 0
            rectangle_min_size = 2
            rectangle_max_size = 5
            min_height = 0.14
            max_height = 0.15
            terrain_utils.discrete_obstacles_terrain_cells(
                terrain,
                min_height,
                max_height,
                rectangle_min_size,
                rectangle_max_size,
                num_rectangles,
                platform_size=3.0,
            )
        elif choice < self.proportions[6]:
            terrain_utils.stepping_stones_terrain(
                terrain,
                stone_size=stepping_stones_size,
                stone_distance=stone_distance,
                max_height=0.0,
                platform_size=4.0,
            )
        elif choice < self.proportions[7]:
            gap_terrain(terrain, gap_size=gap_size, platform_size=3.0)
        else:
            pit_terrain(terrain, depth=pit_depth, platform_size=4.0)

        return terrain

    def add_terrain_to_map(self, terrain, row, col):
        i = row
        j = col
        # map coordinate system
        start_x = self.border + i * self.length_per_env_pixels
        end_x = self.border + (i + 1) * self.length_per_env_pixels
        start_y = self.border + j * self.width_per_env_pixels
        end_y = self.border + (j + 1) * self.width_per_env_pixels
        self.height_field_raw[start_x:end_x, start_y:end_y] = terrain.height_field_raw

        env_origin_x = (i + 0.5) * self.env_length
        env_origin_y = (j + 0.5) * self.env_width
        x1 = int((self.env_length / 2.0 - 1) / terrain.horizontal_scale)
        x2 = int((self.env_length / 2.0 + 1) / terrain.horizontal_scale)
        y1 = int((self.env_width / 2.0 - 1) / terrain.horizontal_scale)
        y2 = int((self.env_width / 2.0 + 1) / terrain.horizontal_scale)
        env_origin_z = (
            np.max(terrain.height_field_raw[x1:x2, y1:y2]) * terrain.vertical_scale
        )
        self.env_origins[i, j] = [env_origin_x, env_origin_y, env_origin_z]

    def add_blocks(self):
        BLOCKS_PER_AREA = 1.0
        BLOCK_HEIGHT = 0.1
        DIST_THRESH = 1.0
        POTENTIAL_DIMS = [(0.15, 0.15), (0.15, 0.3), (0.3, 0.15)]

        x0 = np.amin([i[0] for i in self.vertices if i[2] > 0.1])
        x1 = np.amax([i[0] for i in self.vertices if i[2] > 0.1])
        y0 = np.amin([i[1] for i in self.vertices if i[2] > 0.1])
        y1 = np.amax([i[1] for i in self.vertices if i[2] > 0.1])

        area = (x1 - x0) * (y1 - y0)
        num_blocks = int(area * BLOCKS_PER_AREA)
        # A block is an x, y, s1, s2, and h
        blocks = []
        np.random.seed(int(os.environ["ISAAC_SEED"]))
        for idx in range(num_blocks):
            success = False
            while not success:
                s1, s2 = POTENTIAL_DIMS[np.random.randint(3)]
                x = np.random.rand() * (x1 - x0) + x0
                y = np.random.rand() * (y1 - y0) + y0
                if np.linalg.norm(np.array([x, y]) - np.array([x0, y0])) < 3:
                    continue
                new_block = (x, y, s1, s2, BLOCK_HEIGHT)
                if blocks:
                    blocks_arr = np.array(blocks)
                    new_block_arr = np.array(new_block)
                    diff = blocks_arr - new_block_arr
                    if min(min(diff[:, 0]), min(diff[:, 1])) > DIST_THRESH:
                        continue
                blocks.append(new_block)
                success = True

        for block in blocks:
            self.add_block(*block)

    def add_block(self, x0, y0, s1, s2, h):
        # A rectangular prism has 8 vertices
        new_vertices = [
            (x0, y0, 0.0),
            (x0 + s1, y0, 0.0),
            (x0, y0 + s2, 0.0),
            (x0 + s1, y0 + s2, 0.0),
            (x0, y0, h),
            (x0 + s1, y0, h),
            (x0, y0 + s2, h),
            (x0 + s1, y0 + s2, h),
        ]
        # Spam every possible combination
        new_triangles = list(permutations(range(8), 3))
        self.triangles = np.concatenate(
            [
                self.triangles,
                np.array(new_triangles, dtype=np.uint32) + self.vertices.shape[0],
            ]
        )
        self.vertices = np.concatenate(
            [self.vertices, np.array(new_vertices, dtype=np.float32)]
        )


def gap_terrain(terrain, gap_size, platform_size=1.0):
    gap_size = int(gap_size / terrain.horizontal_scale)
    platform_size = int(platform_size / terrain.horizontal_scale)

    center_x = terrain.length // 2
    center_y = terrain.width // 2
    x1 = (terrain.length - platform_size) // 2
    x2 = x1 + gap_size
    y1 = (terrain.width - platform_size) // 2
    y2 = y1 + gap_size

    terrain.height_field_raw[
        center_x - x2 : center_x + x2, center_y - y2 : center_y + y2
    ] = -1000
    terrain.height_field_raw[
        center_x - x1 : center_x + x1, center_y - y1 : center_y + y1
    ] = 0


def pit_terrain(terrain, depth, platform_size=1.0):
    depth = int(depth / terrain.vertical_scale)
    platform_size = int(platform_size / terrain.horizontal_scale / 2)
    x1 = terrain.length // 2 - platform_size
    x2 = terrain.length // 2 + platform_size
    y1 = terrain.width // 2 - platform_size
    y2 = terrain.width // 2 + platform_size
    terrain.height_field_raw[x1:x2, y1:y2] = -depth
