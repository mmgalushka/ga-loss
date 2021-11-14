"""
Steps for computing Hungarian loss.
"""

import tensorflow as tf

from . import ZERO, ONE
from .ops import (
    count_zeros_in_rows,
    count_zeros_in_cols,
    get_row_mask_with_max_zeros,
    get_col_mask_with_max_zeros,
    expand_item_mask,
)


def reduce_rows(matrix: tf.Tensor) -> tf.Tensor:
    """Subtracts the minimum value from each row.

    Example:
    >>> matrix = tf.Variable(
    >>>    [[[ 30., 25., 10.],
    >>>      [ 15., 10., 20.],
    >>>      [ 25., 20., 15.]]]
    >>> )
    >>> reduce_rows(matrix)

    >>> tf.Tensor(
    >>>     [[[20. 15.  0.]
    >>>       [ 5.  0. 10.]
    >>>       [10.  5.  0.]]], shape=(1, 3, 3), dtype=float16)

    Args:
        matrix:
            The 3D-tensor [batch, rows, columns] of floats to reduce.

    Returns:
        A new tensor with reduced values of the same shape as
        the input tensor.
    """
    return tf.cast(
        tf.subtract(
            matrix, tf.reshape(tf.reduce_min(matrix, axis=2), (-1, 1))
        ),
        tf.float16,
    )


def reduce_cols(matrix: tf.Tensor) -> tf.Tensor:
    """Subtracts the minimum value from each column.

    Example:
    >>> matrix = tf.Variable(
    >>>    [[[ 30., 25., 10.],
    >>>      [ 15., 10., 20.],
    >>>      [ 25., 20., 15.]]]
    >>> )
    >>> reduce_cols(matrix)

    >>> tf.Tensor(
    >>>     [[[15. 15.  0.]
    >>>       [ 0.  0. 10.]
    >>>       [10. 10.  5.]]], shape=(1, 3, 3), dtype=float16)

    Args:
        matrix:
            The 3D-tensor [batch, rows, columns] of floats to reduce.

    Returns:
        A new tensor with reduced values of the same shape as
        the input tensor.
    """
    return tf.cast(
        tf.subtract(matrix, tf.reduce_min(matrix, axis=1)), tf.float16
    )


def scratch_matrix(matrix: tf.Tensor) -> tf.Tensor:
    """Creates the mask for rows and columns which are covering all
    zeros in the matrix.

    Example:
    >>> matrix = tf.Variable(
    >>>    [[[15., 15.,  0.],
    >>>      [ 0.,  0., 10.],
    >>>      [ 5.,  5.,  0.]]]
    >>> )
    >>> scratch_row(matrix)

    >>> (<tf.Tensor: shape=(3, 1), dtype=bool, numpy=
    >>>     array([[False],
    >>>            [ True],
    >>>            [False]])>,
    >>>  <tf.Tensor: shape=(1, 3), dtype=bool, numpy=
    >>>     array([[False, False,  True]])>)

    Args:
        matrix:
            The 3D-tensor [batch, rows, columns] of floats to scrarch.

    Returns:
        scratched_rows_mask:
            The 2D-tensor row mask, where `True` values indicates the
            scratched rows and `False` intact rows accordingly.
        scratched_cols_mask:
            The 2D-tensor column mask, where `True` values indicates the
            scratched columns and `False` intact columns accordingly.
    """

    def scratch_row(zeros_mask, scratched_rows_mask, scratched_cols_mask):
        scratched_row_mask = get_row_mask_with_max_zeros(zeros_mask)
        new_scratched_rows_mask = tf.logical_or(
            scratched_rows_mask, scratched_row_mask
        )
        new_zeros_mask = tf.logical_and(
            zeros_mask, tf.logical_not(expand_item_mask(scratched_row_mask))
        )
        return new_zeros_mask, new_scratched_rows_mask, scratched_cols_mask

    def scratch_col(zeros_mask, scratched_rows_mask, scratched_cols_mask):
        scratched_col_mask = get_col_mask_with_max_zeros(zeros_mask)
        new_scratched_cols_mask = tf.logical_or(
            scratched_cols_mask, scratched_col_mask
        )
        new_zeros_mask = tf.logical_and(
            zeros_mask, tf.logical_not(expand_item_mask(scratched_col_mask))
        )
        return new_zeros_mask, scratched_rows_mask, new_scratched_cols_mask

    def body(zeros_mask, scratched_rows_mask, scratched_cols_mask):
        return tf.cond(
            tf.math.greater(
                tf.reduce_max(count_zeros_in_rows(zeros_mask)),
                tf.reduce_max(count_zeros_in_cols(zeros_mask)),
            ),
            true_fn=lambda: scratch_row(
                zeros_mask, scratched_rows_mask, scratched_cols_mask
            ),
            false_fn=lambda: scratch_col(
                zeros_mask, scratched_rows_mask, scratched_cols_mask
            ),
        )

    def condition(zeros_mask, scratched_rows_mask, scratched_cols_mask):
        return tf.reduce_any(zeros_mask)

    _, num_of_rows, num_of_cols = matrix.shape
    _, scratched_rows_mask, scratched_cols_mask = tf.while_loop(
        condition,
        body,
        [
            tf.math.equal(matrix, ZERO),
            tf.zeros((num_of_rows, 1), tf.bool),
            tf.zeros((1, num_of_cols), tf.bool),
        ],
    )

    return scratched_rows_mask, scratched_cols_mask


def is_optimal_assignment(
    scratched_rows_mask: tf.Tensor, scratched_cols_mask: tf.Tensor
) -> tf.Tensor:
    """Test if we can achieve the optimal assignment.

    We can achieve the optimal assignment if the combined number of
    scratched columns and rows equals to the matrix dimensions (since
    matrix is square, dimension side does not matter.)

    Example:

        Optimal assignment:
        >>> scratched_rows_mask = tf.constant(
        >>>    [[False], [True], [False]], tf.bool)
        >>> scratched_cols_mask = tf.constant(
        >>>    [[True, False, True]])
        >>> is_optimal_assignment(scratched_rows_mask, scratched_cols_mask)

        >>> tf.Tensor(True, shape=(), dtype=bool)

        Not optimal assignment:
        >>> scratched_rows_mask = tf.constant(
        >>>    [[False], [True], [False]], tf.bool)
        >>> scratched_cols_mask = tf.constant(
        >>>    [[True, False, True]])
        >>> is_optimal_assignment(scratched_rows_mask, scratched_cols_mask)

        >>> tf.Tensor(False, shape=(), dtype=bool)

    Args:
        scratched_rows_mask:
            The 2D-tensor row mask, where `True` values indicates the
            scratched rows and `False` intact rows accordingly.
        scratched_cols_mask:
            The 2D-tensor column mask, where `True` values indicates the
            scratched columns and `False` intact columns accordingly.

    Returns:
        The boolean tensor, where `True` indicates the optimal assignment
        and `False` otherwise.
    """
    assert scratched_rows_mask.shape[0] == scratched_cols_mask.shape[1]
    n = scratched_rows_mask.shape[0]
    number_of_lines_covering_zeros = tf.add(
        tf.reduce_sum(tf.cast(scratched_rows_mask, tf.float16)),
        tf.reduce_sum(tf.cast(scratched_cols_mask, tf.float16)),
    )
    return tf.equal(n, number_of_lines_covering_zeros)


def shift_zeros(matrix, scratched_rows_mask, scratched_cols_mask):
    """Shifts zeros in not optimal mask.

    Example:

        Optimal assignment:
        >>> matrix = tf.constant(
        >>>    [[[ 30., 25., 10.],
        >>>      [ 15., 10., 20.],
        >>>      [ 25., 20., 15.]]], tf.float16
        >>> )
        >>> scratched_rows_mask = tf.constant(
        >>>    [[False], [True], [False]], tf.bool)
        >>> scratched_cols_mask = tf.constant(
        >>>    [[False, False, True]])
        >>> shift_zeros(matrix, scratched_rows_mask, scratched_cols_mask)

        >>> (<tf.Tensor:
        >>>       [[[10., 10.,  0.],
        >>>         [ 0.,  0., 15.],
        >>>         [ 0.,  0.,  0.]]], shape=(1, 3, 3) dtype=float16)>,
        >>> <tf.Tensor:
        >>>       [[False],
        >>>        [ True],
        >>>        [False]], shape=(3, 1), dtype=bool>,
        >>> <tf.Tensor:
        >>>       [[False, False,  True]], shape=(1, 3), dtype=bool>)

    Args:
        matrix:
            The 3D-tensor [batch, rows, columns] of floats with reduced
            values.
        scratched_rows_mask:
            The 2D-tensor row mask, where `True` values indicates the
            scratched rows and `False` intact rows accordingly.
        scratched_cols_mask:
            The 2D-tensor column mask, where `True` values indicates the
            scratched columns and `False` intact columns accordingly.

    Returns:
        matrix:
            The 3D-tensor [batch, rows, columns] of floats with shifted
            zeros.
        scratched_rows_mask:
            The same as input.
        scratched_cols_mask:
            The same as input
    """
    cross_mask = tf.cast(
        tf.logical_and(scratched_rows_mask, scratched_cols_mask),
        tf.float16,
    )
    inline_mask = tf.cast(
        tf.logical_or(
            tf.logical_and(
                scratched_rows_mask, tf.logical_not(scratched_cols_mask)
            ),
            tf.logical_and(
                tf.logical_not(scratched_rows_mask), scratched_cols_mask
            ),
        ),
        tf.float16,
    )
    outline_mask = tf.cast(
        tf.logical_not(
            tf.logical_or(scratched_rows_mask, scratched_cols_mask)
        ),
        tf.float16,
    )

    outline_min_value = tf.reduce_min(
        tf.math.add(
            tf.math.multiply(
                tf.math.subtract(ONE, outline_mask), tf.float16.max
            ),
            tf.math.multiply(matrix, outline_mask),
        )
    )

    cross_matrix = tf.add(
        tf.multiply(matrix, cross_mask),
        tf.multiply(outline_min_value, cross_mask),
    )
    inline_matrix = tf.multiply(matrix, inline_mask)
    outline_matrix = tf.subtract(
        tf.multiply(matrix, outline_mask),
        tf.multiply(outline_min_value, outline_mask),
    )

    return (
        tf.math.add(cross_matrix, tf.math.add(inline_matrix, outline_matrix)),
        scratched_rows_mask,
        scratched_cols_mask,
    )


def reduce_matrix(matrix):
    """Reduce matrix suitable to perform the optimal assignment.

    Example:
        >>> matrix = tf.constant(
        >>>    [[[ 30., 25., 10.],
        >>>      [ 15., 10., 20.],
        >>>      [ 25., 20., 15.]]], tf.float16
        >>> )
        >>> reduce_matrix(matrix)

    """

    def body(matrix, scratched_rows_mask, scratched_cols_mask):
        new_matrix = reduce_rows(matrix)
        new_matrix = reduce_cols(new_matrix)
        scratched_rows_mask, scratched_cols_mask = scratch_matrix(new_matrix)

        return tf.cond(
            is_optimal_assignment(scratched_rows_mask, scratched_cols_mask),
            true_fn=lambda: [
                new_matrix,
                scratched_rows_mask,
                scratched_cols_mask,
            ],
            false_fn=lambda: shift_zeros(
                new_matrix, scratched_rows_mask, scratched_cols_mask
            ),
        )

    def condition(
        matrix, scratched_rows_mask, scratched_cols_mask
    ):  # pylint: disable=unused-argument
        return tf.logical_not(
            is_optimal_assignment(scratched_rows_mask, scratched_cols_mask)
        )

    _, num_of_rows, num_of_cols = matrix.shape
    reduced_matrix, _, _ = tf.while_loop(
        condition,
        body,
        [
            matrix,
            tf.zeros((num_of_rows, 1), tf.bool),
            tf.zeros((1, num_of_cols), tf.bool),
        ],
    )

    return reduced_matrix
