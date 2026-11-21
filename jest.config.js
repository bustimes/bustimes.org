module.exports = {
    roots: ["<rootDir>/frontend"],
    transform: { "\\.m?[jt]sx?$": "babel-jest" },
    transformIgnorePatterns: [`/node_modules/(?!wouter|preact)`],
    testEnvironmentOptions: { customExportConditions: ["node", "require"] },
    moduleNameMapper: {
        "^react(-dom)?$": "<rootDir>/frontend/js/preact-compat.ts",
        "^react-dom/client$": "preact/compat/client",
        "^react/jsx-runtime$": "preact/jsx-runtime",
    },
};
