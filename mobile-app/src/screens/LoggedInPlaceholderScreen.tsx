import { StyleSheet, Text, View } from "react-native";

export default function LoggedInPlaceholderScreen() {
  return (
    <View style={styles.screen}>
      <Text style={styles.text}>Logged in — My Tasks / Home coming in a later phase.</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: "#0B1120",
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
  },
  text: {
    color: "#fff",
    fontSize: 16,
    textAlign: "center",
  },
});
