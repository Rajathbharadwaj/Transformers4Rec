#
# Copyright (c) 2021, NVIDIA CORPORATION.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import pytest

pytorch = pytest.importorskip("torch")
torch4rec = pytest.importorskip("transformers4rec.torch")


def test_base_block(torch_yoochoose_tabular_features):
    block = torch_yoochoose_tabular_features >> torch4rec.MLPBlock([64, 32])

    embedding_block = block.get_children_by_class_name(list(block), "EmbeddingFeatures")[0]

    assert isinstance(embedding_block, torch4rec.EmbeddingFeatures)


def test_sequential_block(torch_yoochoose_tabular_features):
    block = torch4rec.SequentialBlock(
        torch_yoochoose_tabular_features,
        torch4rec.MLPBlock([64, 32]),
        torch4rec.Block(pytorch.nn.Dropout(0.5), [None, 32]),
    )

    output_size = block.output_size()
    assert list(output_size) == [-1, 32]

    embedding_block = block.get_children_by_class_name(list(block), "EmbeddingFeatures")[0]
    assert isinstance(embedding_block, torch4rec.EmbeddingFeatures)


def test_sequential_block_with_output_size(torch_yoochoose_tabular_features):
    block = torch4rec.SequentialBlock(
        torch_yoochoose_tabular_features,
        torch4rec.MLPBlock([64, 32]),
        pytorch.nn.Dropout(0.5),
        output_size=[None, 32],
    )

    output_size = block.output_size()
    assert list(output_size) == [None, 32]


def test_sequential(torch_yoochoose_tabular_features):
    inputs = torch_yoochoose_tabular_features
    block = pytorch.nn.Sequential(*torch4rec.build_blocks(inputs, torch4rec.MLPBlock([64, 32])))
    block2 = pytorch.nn.Sequential(inputs, torch4rec.MLPBlock([64, 32]).to_module(inputs))

    assert isinstance(block, pytorch.nn.Sequential)
    assert isinstance(block2, pytorch.nn.Sequential)
