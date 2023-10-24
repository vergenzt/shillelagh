"""
An FDW.
"""

from collections import defaultdict
from typing import (
    Any,
    DefaultDict,
    Dict,
    Iterator,
    List,
    Optional,
    Set,
    Tuple,
    TypedDict,
)

from multicorn import ForeignDataWrapper, Qual, SortKey

from shillelagh.adapters.registry import registry
from shillelagh.fields import Order
from shillelagh.filters import Operator
from shillelagh.lib import deserialize, get_bounds
from shillelagh.typing import RequestedOrder, Row

operator_map = {
    "=": Operator.EQ,
    ">": Operator.GT,
    "<": Operator.LT,
    ">=": Operator.GE,
    "<=": Operator.LE,
}


def get_all_bounds(quals: List[Qual]) -> DefaultDict[str, Set[Tuple[Operator, Any]]]:
    """
    Convert list of ``Qual`` into a set of operators for each column.
    """
    all_bounds: DefaultDict[str, Set[Tuple[Operator, Any]]] = defaultdict(set)
    for qual in quals:
        if operator := operator_map.get(qual.operator):
            all_bounds[qual.field_name].add((operator, qual.value))

    return all_bounds


class OptionsType(TypedDict):
    """
    Type for OPTIONS.
    """

    adapter: str
    args: str


class MulticornForeignDataWrapper(ForeignDataWrapper):
    """
    A FDW that dispatches queries to adapters.
    """

    def __init__(self, options: OptionsType, columns: Dict[str, str]):
        super().__init__(options, columns)

        deserialized_args = deserialize(options["args"])
        self.adapter = registry.load(options["adapter"])(*deserialized_args)
        self.columns = self.adapter.get_columns()

    def execute(
        self,
        quals: List[Qual],
        columns: List[str],
        sortkeys: Optional[List[SortKey]] = None,
    ) -> Iterator[Row]:
        """
        Execute a query.
        """
        all_bounds = get_all_bounds(quals)
        bounds = get_bounds(self.columns, all_bounds)

        order: List[Tuple[str, RequestedOrder]] = [
            (key.attname, Order.DESCENDING if key.is_reversed else Order.ASCENDING)
            for key in sortkeys or []
        ]

        kwargs = (
            {"requested_columns": columns}
            if self.adapter.supports_requested_columns
            else {}
        )

        return self.adapter.get_rows(bounds, order, **kwargs)

    def can_sort(self, sortkeys: List[SortKey]) -> List[SortKey]:
        """
        Return a list of sorts the adapter can perform.
        """

        def is_sortable(key: SortKey) -> bool:
            """
            Return if a given sort key can be enforced by the adapter.
            """
            if key.attname not in self.columns:
                return False

            order = self.columns[key.attname].order
            return (
                order == Order.ANY
                or (order == Order.ASCENDING and not key.is_reversed)
                or (order == Order.DESCENDING and key.is_reversed)
            )

        return [key for key in sortkeys if is_sortable(key)]

    def insert(self, values: Row) -> Row:
        rowid = self.adapter.insert_row(values)
        values["rowid"] = rowid
        return values

    def delete(self, oldvalues: Row) -> None:
        rowid = oldvalues["rowid"]
        self.adapter.delete_row(rowid)

    def update(self, oldvalues: Row, newvalues: Row) -> Row:
        rowid = newvalues["rowid"]
        self.adapter.update_row(rowid, newvalues)
        return newvalues

    @property
    def rowid_column(self):
        return "rowid"

    @classmethod
    def import_schema(  # pylint: disable=too-many-arguments
        cls,
        schema: str,
        srv_options: Dict[str, str],
        options: Dict[str, str],
        restriction_type: Optional[str],
        restricts: List[str],
    ):
        return []
