"""Reference normalization for semantic AAS discovery."""

from collections.abc import Iterable

from semantic_model import ElementRef, ResolvedReference


def reference_keys(reference: object) -> list[dict]:
    if not isinstance(reference, dict):
        return []
    keys = reference.get("keys", [])
    return [key for key in keys if isinstance(key, dict)] if isinstance(keys, list) else []


class ReferenceResolver:
    """Resolve AAS model/global references against a catalog-build snapshot."""

    def __init__(self) -> None:
        self.elements_by_ref: dict[ElementRef, dict] = {}
        self.canonical_by_identifier: dict[str, str] = {}

    def add_element(self, element_ref: ElementRef, element: dict) -> None:
        self.elements_by_ref[element_ref] = element

    def add_asset_identity(
        self, *, aas_id: str, global_asset_id: str, id_short: str | None = None
    ) -> None:
        if not global_asset_id:
            return
        self.canonical_by_identifier[global_asset_id] = global_asset_id
        if aas_id:
            self.canonical_by_identifier[aas_id] = global_asset_id
        if id_short:
            self.canonical_by_identifier.setdefault(id_short, global_asset_id)

    @staticmethod
    def _element_ref(keys: Iterable[dict]) -> ElementRef | None:
        keys = list(keys)
        submodel_index = next(
            (
                index
                for index, key in enumerate(keys)
                if str(key.get("type") or "").lower() == "submodel"
            ),
            None,
        )
        if submodel_index is None:
            return None
        submodel_id = str(keys[submodel_index].get("value") or "").strip()
        path = ".".join(
            str(key.get("value") or "").strip()
            for key in keys[submodel_index + 1 :]
            if str(key.get("value") or "").strip()
        )
        if not submodel_id or not path:
            return None
        return ElementRef(submodel_id, path)

    def resolve_reference(self, reference: object) -> ResolvedReference:
        keys = reference_keys(reference)
        reference_type = (
            str(reference.get("type") or "") if isinstance(reference, dict) else ""
        )
        element_ref = self._element_ref(keys)
        if element_ref is not None:
            element = self.elements_by_ref.get(element_ref)
            if element is not None:
                canonical = self._canonical_from_element(element)
                return ResolvedReference(
                    reference_type=reference_type or "ModelReference",
                    canonical_id=canonical,
                    element_ref=element_ref,
                    element=element,
                )
            return ResolvedReference(
                reference_type=reference_type or "ModelReference",
                element_ref=element_ref,
            )

        values = [
            str(key.get("value") or "").strip()
            for key in keys
            if str(key.get("value") or "").strip()
        ]
        for value in reversed(values):
            canonical = self.canonical_by_identifier.get(value)
            if canonical:
                return ResolvedReference(
                    reference_type=reference_type or "ExternalReference",
                    canonical_id=canonical,
                )
        if values and (
            reference_type.lower() in {"externalreference", "globalreference"}
            or any(
                str(key.get("type") or "").lower() == "globalreference"
                for key in keys
            )
        ):
            return ResolvedReference(
                reference_type=reference_type or "ExternalReference",
                canonical_id=values[-1],
            )
        return ResolvedReference(reference_type=reference_type or "Unknown")

    def _canonical_from_element(self, element: dict) -> str | None:
        for field_name in ("globalAssetId", "global_asset_id"):
            value = element.get(field_name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def canonical_entity_id(self, reference: object) -> str | None:
        return self.resolve_reference(reference).canonical_id


def resolve_reference(
    reference: object, resolver: ReferenceResolver
) -> ResolvedReference:
    return resolver.resolve_reference(reference)


def canonical_entity_id(
    reference: object, resolver: ReferenceResolver
) -> str | None:
    return resolver.canonical_entity_id(reference)
