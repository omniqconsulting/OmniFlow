import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import GenericEntityScreen, { type FieldDef } from "../components/GenericEntityScreen";
import { uomApi, type Uom } from "../api/setup";

type Props = NativeStackScreenProps<AuthStackParamList, "SetupUom">;

const FIELDS: FieldDef[] = [
  { key: "name", label: "Name", type: "text" },
  { key: "abbreviation", label: "Abbreviation", type: "text" },
  { key: "is_active", label: "Active", type: "switch" },
];

export default function UomScreen({ navigation }: Props) {
  return (
    <GenericEntityScreen<Uom>
      title="Units of Measure"
      subtitle="Used across inventory, FMS & sales"
      fields={FIELDS}
      defaultValues={{ name: "", abbreviation: "", is_active: true }}
      api={uomApi}
      rowTitle={(u) => u.name}
      rowSubtitle={(u) => u.abbreviation}
      toFormValues={(u) => ({ name: u.name, abbreviation: u.abbreviation, is_active: u.is_active })}
      onBack={() => navigation.goBack()}
    />
  );
}
