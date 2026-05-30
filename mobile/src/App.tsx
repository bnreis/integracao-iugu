import React, { useState } from "react";
import { StatusBar } from "react-native";
import { NavigationContainer } from "@react-navigation/native";

import LoginScreen from "./screens/LoginScreen";
import AppNavigator from "./navigation/AppNavigator";
import { isAuthenticated } from "./services/api";

export default function App() {
  const [loggedIn, setLoggedIn] = useState(isAuthenticated());

  if (!loggedIn) {
    return (
      <>
        <StatusBar barStyle="light-content" backgroundColor="#1a56db" />
        <LoginScreen onLoginSuccess={() => setLoggedIn(true)} />
      </>
    );
  }

  return (
    <>
      <StatusBar barStyle="light-content" backgroundColor="#1a56db" />
      <NavigationContainer>
        <AppNavigator />
      </NavigationContainer>
    </>
  );
}
