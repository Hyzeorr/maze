""" Contains an implementation of the point net block from https://arxiv.org/abs/1612.00593 and its components. """
from typing import Union, List, Dict, Sequence

import torch
from torch import nn as nn

from maze.core.annotations import override
from maze.core.utils.factory import Factory
from maze.perception.blocks.general.masked_global_pooling import MaskedGlobalPoolingBlock
from maze.perception.blocks.shape_normalization import ShapeNormalizationBlock


class PointNetFeatureTransformNet(nn.Module):
    """ Feature Transform Net as proposed in https://arxiv.org/abs/1612.00593. This Module implements three
        convolutional stacks, each consisting of a 1d Convolution (kernel size =1) followed by an optional batch
        normalization and a specified non-linearity. The resulting output of the convolutions is then pooled in the
        point dimension (N) with the specified pooling method. Next two fully connected layers (again with optional
        batch norm and non linearity) are process the now two dimensional data. Finally one fully connected layer is
        applied before reshaping the data into the output format: BxKxK, where B is the batch dimension, N is the number
        of points and K is the number of features. The input to the module should have the shape BxKxN.

        :param num_features: Number of input features (K).
        :param embedding_dim: The embedding dimension to use (Paper: 1024).
        :param pooling_func_name: A string in ('max', 'mean', 'sum') specifying the pooling function to use. (Paper:
            'max')
        :param use_batch_norm: Specify whether to use batch_norm (like in original paper).
        :param non_lin: The non-linearity to apply after each fully connected layer.

        """

    def __init__(self, num_features: int, embedding_dim: int, pooling_func_name: str, use_batch_norm: bool,
                 non_lin: Union[str, type(nn.Module)]):
        super().__init__()

        # Init class variables
        self._use_batch_norm = use_batch_norm
        self._pooling_func_name = pooling_func_name
        self._num_features = num_features

        # Init convolutions
        self.conv1 = torch.nn.Conv1d(num_features, embedding_dim // 16, 1)
        self.conv2 = torch.nn.Conv1d(embedding_dim // 16, embedding_dim // 8, 1)
        self.conv3 = torch.nn.Conv1d(embedding_dim // 8, embedding_dim, 1)

        # Init fully connected layers
        self.fc1 = nn.Linear(embedding_dim, embedding_dim // 2)
        self.fc2 = nn.Linear(embedding_dim // 2, embedding_dim // 4)
        self.fc3 = nn.Linear(embedding_dim // 4, num_features * num_features)

        # Init batch norm
        if self._use_batch_norm:
            self.bn1 = nn.BatchNorm1d(embedding_dim // 16)
            self.bn2 = nn.BatchNorm1d(embedding_dim // 8)
            self.bn3 = nn.BatchNorm1d(embedding_dim)
            self.bn4 = nn.BatchNorm1d(embedding_dim // 2)
            self.bn5 = nn.BatchNorm1d(embedding_dim // 4)

        # Init non linearity's
        non_lin = Factory(base_type=nn.Module).class_type_from_name(non_lin)
        self.non_lin_1 = non_lin()
        self.non_lin_2 = non_lin()
        self.non_lin_3 = non_lin()
        self.non_lin_4 = non_lin()
        self.non_lin_5 = non_lin()

    def forward(self, input_tensor):
        """Forward pass through the transformer module

        :param input_tensor: Input to the network (BB, KK, NN)
        :return: A transformation matrix of the form (BB, KK, KK)
        """

        batch_size = input_tensor.shape[0]

        # input_tensor: (BB, KK, NN)
        out = self.conv1(input_tensor)
        if self._use_batch_norm and batch_size > 1:
            out = self.bn1(out)
        out = self.non_lin_1(out)

        # out: (BB, embedding_dim // 16, NN)
        out = self.conv2(out)
        if self._use_batch_norm and batch_size > 1:
            out = self.bn2(out)
        out = self.non_lin_2(out)

        # out: (BB, embedding_dim // 8, NN)
        out = self.conv3(out)
        if self._use_batch_norm and batch_size > 1:
            out = self.bn3(out)
        out = self.non_lin_3(out)
        # out: (BB, embedding_dim , NN)

        # Pooling
        # out: (BB, embedding_dim, NN)
        if self._pooling_func_name == 'max':
            out = torch.max(out, -1, keepdim=True)[0]
        elif self._pooling_func_name == 'mean':
            out = torch.mean(out, keepdim=True, dim=-1)
        elif self._pooling_func_name == 'sum':
            out = torch.sum(out, keepdim=True, dim=-1)
        else:
            raise ValueError(f"Pooling function {self._pooling_func_name} is not yet supported!")

        # out: (BB, embedding_dim, 1)
        out = torch.flatten(out, start_dim=-2)

        # out: (BB, embedding_dim)
        out = self.fc1(out)
        if self._use_batch_norm and batch_size > 1:
            out = self.bn4(out)
        out = self.non_lin_4(out)

        # out: (BB, embedding_dim//2)
        out = self.fc2(out)
        if self._use_batch_norm and batch_size > 1:
            out = self.bn5(out)
        out = self.non_lin_5(out)

        # out: (BB, embedding_dim//4)
        out = self.fc3(out)
        # out: (BB, num_features ** 2)

        identity = torch.Tensor(torch.flatten(torch.eye(self._num_features),
                                              start_dim=-2).to(torch.float32).to(out.device))
        identity = identity.repeat(batch_size, 1)
        out = out + identity

        # out: (BB, num_features ** 2)
        out = out.view(-1, self._num_features, self._num_features)
        # out: (BB, num_features, num_features)
        return out


class PointNetBlock(ShapeNormalizationBlock):
    """Perception Block with shape normalization implementing the PointNet mechanics (
    https://arxiv.org/abs/1612.00593 from Stanford University).

    The block processed the input with one input transformation, two 1d convolutions each followed by an optional batch
    normalization and a non linearity. Then an optional feature transformation is applied before a final convolutional
    layer. Lastly a masked global pooling block is used to pool all values in the point dimension resulting in a
    two dimensional vector (batch_dim, feature_dim). The maks for pooling is an optional parameter.

    :param in_keys: One key identifying the input tensors, a second optional one identifying the masking tensor.
    :param out_keys: One key identifying the output tensors.
    :param in_shapes: List of input shapes.
    :param embedding_dim: The embedding dimension to use throughout the block, this is also specifies the dimension of
        the output. (Paper: 1024)
    :param pooling_func_name: A string in ('max', 'mean', 'sum') specifying the pooling function to use. (Paper: 'max')
    :param use_feature_transform: Whether to use the feature transformation after the second convolution. (Paper: True)
    :param use_batch_norm: Specify whether to use batch_norm (is disables for batches of size <2).
    :param non_lin: The non-linearity to apply after each layer.
    """

    def __init__(self, in_keys: Union[str, List[str]], out_keys: Union[str, List[str]],
                 in_shapes: Union[Sequence[int], List[Sequence[int]]], embedding_dim: int, pooling_func_name: str,
                 use_feature_transform: bool, use_batch_norm: bool, non_lin: Union[str, type(nn.Module)]):

        # Infer number of input dimension
        in_keys = in_keys if isinstance(in_keys, List) else [in_keys]
        in_num_dims = 3 if len(in_keys) == 1 else [3, 2]
        super().__init__(in_keys=in_keys, out_keys=out_keys, in_shapes=in_shapes, in_num_dims=in_num_dims,
                         out_num_dims=2)

        # Input parameter assertions
        assert len(self.in_shapes[0]) == 2
        if len(self.in_keys) == 2:
            assert len(self.in_shapes[1]) == 1
            assert self.in_shapes[0][-2] == self.in_shapes[1][-1], f'Point dimension should fit: {self.in_shapes[0]} ' \
                                                                   f'vs {self.in_shapes[1]}'

        # Init class variables
        self.input_units = self.in_shapes[0][-1]
        self._use_feature_transform = use_feature_transform
        self._embedding_dim = embedding_dim
        self._use_batch_norm = use_batch_norm

        self.input_transform = PointNetFeatureTransformNet(
            self.in_shapes[0][-1], non_lin=non_lin, use_batch_norm=self._use_batch_norm, embedding_dim=embedding_dim,
            pooling_func_name=pooling_func_name)
        if self._use_feature_transform:
            self.feature_transform = PointNetFeatureTransformNet(
                embedding_dim // 16, non_lin=non_lin, use_batch_norm=self._use_batch_norm, embedding_dim=embedding_dim,
                pooling_func_name=pooling_func_name
            )

        self.conv1 = torch.nn.Conv1d(self.in_shapes[0][-1], embedding_dim // 16, 1)
        self.conv2 = torch.nn.Conv1d(embedding_dim // 16, embedding_dim // 8, 1)
        self.conv3 = torch.nn.Conv1d(embedding_dim // 8, embedding_dim, 1)

        if self._use_batch_norm:
            self.bn1 = nn.BatchNorm1d(embedding_dim // 16)
            self.bn2 = nn.BatchNorm1d(embedding_dim // 8)
            self.bn3 = nn.BatchNorm1d(embedding_dim)
        else:
            self.bn1 = nn.Identity()
            self.bn2 = nn.Identity()
            self.bn3 = nn.Identity()

        # Set up the pooling operation with masking
        self.use_masking = len(self.in_keys) > 1
        tensor_in_shape = (self.in_shapes[0][0], embedding_dim)
        self.pooling_block = MaskedGlobalPoolingBlock(
            in_keys='in_tensor' if not self.use_masking else ['in_tensor', self.in_keys[1]],
            in_shapes=tensor_in_shape if not self.use_masking else [tensor_in_shape, self.in_shapes[1]],
            pooling_func=pooling_func_name, pooling_dim=-2, out_keys='masking_out'
        )

        self.pooling_func_str = pooling_func_name
        self.non_lin_cls = Factory(base_type=nn.Module).class_type_from_name(non_lin)
        self.non_lin_1 = self.non_lin_cls()
        self.non_lin_2 = self.non_lin_cls()

    @override(ShapeNormalizationBlock)
    def normalized_forward(self, block_input: Dict[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
        """implementation of :class:`~maze.perception.blocks.shape_normalization.ShapeNormalizationBlock` interface
        """

        # check input tensor
        input_tensor = block_input[self.in_keys[0]]
        assert input_tensor.ndim == self.in_num_dims[0]
        assert input_tensor.shape[-1] == self.input_units, f'failed for obs {self.in_keys[0]} because ' \
                                                           f'{input_tensor.shape[-1]} != {self.input_units}'
        # forward pass
        # input: (BB, NN, KK)
        input_tensor = input_tensor.transpose(2, 1)
        # input_tensor: (BB, KK, NN)
        input_transformation_matrices = self.input_transform(input_tensor)
        # input_transformation_matrices: (BB, KK, KK)

        out = torch.bmm(input_transformation_matrices, input_tensor)
        # out: (BB, KK, NN)

        out = self.non_lin_1(self.bn1(self.conv1(out)))
        # out: (BB, embedding_dim // 16, NN)

        if self._use_feature_transform:
            feature_transformation_matrices = self.feature_transform(out)
            # feature_transformation_matrices: (BB, embedding_dim // 16,  embedding_dim // 16)
            out = torch.bmm(feature_transformation_matrices, out)
            # out: (BB, embedding_dim // 16, NN)

        out = self.non_lin_2(self.bn2(self.conv2(out)))
        # out: (BB, embedding_dim // 8, NN)
        out = self.bn3(self.conv3(out))
        # out: (BB, embedding_dim, NN)

        masking_input = {'in_tensor': out.transpose(2, 1)}
        if self.use_masking:
            masking_input[self.in_keys[1]] = block_input[self.in_keys[1]]

        output_tensor = self.pooling_block(masking_input)['masking_out']
        # output_tensor: (BB, embedding_dim)

        # check output tensor
        assert output_tensor.ndim == self.out_num_dims[0]
        assert output_tensor.shape[-1] == self._embedding_dim

        return {self.out_keys[0]: output_tensor}

    def __repr__(self):
        txt = f"{PointNetBlock.__name__}({self.non_lin_cls.__name__})"
        txt += f"\n\tembedding_dim: {self._embedding_dim}"
        txt += f"\n\tpooling_func_str: {self.pooling_func_str}"
        txt += f"\n\tuse_feature_transform: {self._use_feature_transform}"
        txt += f"\n\tuse_batch_norm: {self._use_batch_norm}"
        txt += f"\n\tOut Shapes: {self.out_shapes()}"
        return txt