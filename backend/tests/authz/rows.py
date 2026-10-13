"""Every matrix row: parts A and B together."""

from tests.authz.matrix import Row
from tests.authz.rows_a import ROWS_A
from tests.authz.rows_b import ROWS_B

ALL_ROWS: tuple[Row, ...] = ROWS_A + ROWS_B
