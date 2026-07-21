import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import GenericEntityScreen, { type FieldDef } from "../components/GenericEntityScreen";
import { productsApi, type EndProduct } from "../api/setup";

type Props = NativeStackScreenProps<AuthStackParamList, "SetupProducts">;

const FIELDS: FieldDef[] = [
  { key: "name", label: "Name", type: "text" },
  { key: "sku_code", label: "SKU Code", type: "text" },
  { key: "unit", label: "Unit", type: "text" },
  { key: "description", label: "Description", type: "text", multiline: true },
  { key: "is_active", label: "Active", type: "switch" },
];

export default function ProductsScreen({ navigation }: Props) {
  return (
    <GenericEntityScreen<EndProduct>
      title="End Products"
      subtitle="SKUs used across FMS, Sales & Inventory"
      fields={FIELDS}
      defaultValues={{ name: "", sku_code: "", unit: "", description: "", is_active: true }}
      api={productsApi}
      rowTitle={(p) => p.name}
      rowSubtitle={(p) => [p.sku_code, p.unit].filter(Boolean).join(" · ") || (p.is_active ? "Active" : "Inactive")}
      toFormValues={(p) => ({
        name: p.name, sku_code: p.sku_code ?? "", unit: p.unit ?? "", description: p.description ?? "", is_active: p.is_active,
      })}
      onBack={() => navigation.goBack()}
    />
  );
}
