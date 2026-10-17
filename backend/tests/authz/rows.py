"""Every matrix row: parts A, B and C together."""

from tests.authz.matrix import Row
from tests.authz.rows_a import ROWS_A
from tests.authz.rows_b import ROWS_B
from tests.authz.rows_c import ROWS_C

ALL_ROWS: tuple[Row, ...] = ROWS_A + ROWS_B + ROWS_C
