import { StyleSheet, Text, TouchableOpacity, View } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import { useTheme, type ThemeColors } from "../theme/ThemeContext";

type Props = NativeStackScreenProps<AuthStackParamList, "Organization">;

export default function OrganizationScreen({ navigation, route }: Props) {
  const { user } = route.params;
  const { colors } = useTheme();
  const styles = makeStyles(colors);
  const isAdminOrManager = user.role === "ADMIN" || user.role === "MANAGER";

  const tiles = [
    {
      icon: "👥", iconBg: "rgba(234,179,8,0.14)", iconFg: "#eab308", title: "Employees",
      sub: "Directory & org chart", onPress: () => navigation.navigate("SetupEmployees", { user }),
    },
    {
      icon: "📍", iconBg: "rgba(45,212,191,0.14)", iconFg: colors.teal, title: "Attendance & Leave",
      sub: isAdminOrManager ? "Punch, leave & team approvals" : "Punch in/out & apply leave",
      onPress: () => navigation.navigate("Attendance", { user }),
    },
    {
      icon: "🎓", iconBg: "rgba(102,87,242,0.14)", iconFg: colors.indigo, title: "Training",
      sub: "Knowledge & materials", onPress: () => navigation.navigate("OrgTraining", { user }),
    },
  ];

  return (
    <View style={styles.screen}>
      <View style={styles.topBar}>
        <TouchableOpacity style={styles.backButton} onPress={() => navigation.goBack()}>
          <Text style={styles.backIcon}>‹</Text>
        </TouchableOpacity>
        <View>
          <Text style={styles.title}>Organization</Text>
          <Text style={styles.subtitle}>{isAdminOrManager ? "Admin view" : "Employee view"}</Text>
        </View>
      </View>

      <View style={styles.tileGrid}>
        {tiles.map((tile) => (
          <TouchableOpacity key={tile.title} style={styles.tile} onPress={tile.onPress}>
            <View style={[styles.tileIcon, { backgroundColor: tile.iconBg }]}>
              <Text style={{ fontSize: 22 }}>{tile.icon}</Text>
            </View>
            <Text style={styles.tileTitle}>{tile.title}</Text>
            <Text style={styles.tileSub}>{tile.sub}</Text>
          </TouchableOpacity>
        ))}
      </View>
    </View>
  );
}

function makeStyles(colors: ThemeColors) {
  return StyleSheet.create({
    screen: { flex: 1, backgroundColor: colors.screenBg },
    topBar: { paddingTop: 58, paddingHorizontal: 20, paddingBottom: 10, flexDirection: "row", alignItems: "center", gap: 12 },
    backButton: { width: 34, height: 34, borderRadius: 10, backgroundColor: colors.cardBg, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
    backIcon: { fontSize: 16, color: colors.textSecondary },
    title: { fontSize: 17.5, fontWeight: "800", color: colors.textPrimary },
    subtitle: { fontSize: 11, color: colors.textMuted, marginTop: 1 },
    tileGrid: { flexDirection: "row", flexWrap: "wrap", gap: 12, padding: 20 },
    tile: {
      width: "100%", minHeight: 100, borderRadius: 18, padding: 18,
      backgroundColor: colors.cardBg, borderWidth: 1, borderColor: colors.border,
      justifyContent: "flex-end", gap: 8,
    },
    tileIcon: { width: 44, height: 44, borderRadius: 12, alignItems: "center", justifyContent: "center" },
    tileTitle: { fontSize: 14.5, fontWeight: "700", color: colors.textPrimary },
    tileSub: { fontSize: 11.5, color: colors.textSecondary },
  });
}
