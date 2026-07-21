import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import GenericEntityScreen, { type FieldDef } from "../components/GenericEntityScreen";
import { materialsApi, type RawMaterial } from "../api/setup";

type Props = NativeStackScreenProps<AuthStackParamList, "SetupMaterials">;

const FIELDS: FieldDef[] = [
  { key: "name", label: "Name", type: "text" },
  { key: "unit", label: "Unit", type: "text" },
  { key: "major_supplier", label: "Major Supplier", type: "text" },
  { key: "description", label: "Description", type: "text", multiline: true },
  { key: "is_active", label: "Active", type: "switch" },
];

export default function MaterialsScreen({ navigation }: Props) {
  return (
    <GenericEntityScreen<RawMaterial>
      title="Raw Materials"
      subtitle="Used across FMS & inventory linking"
      fields={FIELDS}
      defaultValues={{ name: "", unit: "", major_supplier: "", description: "", is_active: true }}
      api={materialsApi}
      rowTitle={(m) => m.name}
      rowSubtitle={(m) => [m.unit, m.major_supplier].filter(Boolean).join(" · ") || (m.is_active ? "Active" : "Inactive")}
      toFormValues={(m) => ({
        name: m.name, unit: m.unit ?? "", major_supplier: m.major_supplier ?? "", description: m.description ?? "", is_active: m.is_active,
      })}
      onBack={() => navigation.goBack()}
    />
  );
}
