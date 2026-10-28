module.exports = {
    roots: ["<rootDir>/frontend"],
    transformIgnorePatterns: [`/node_modules/(?!wouter)`],
    testEnvironmentOptions: { customExportConditions: ["node", "require"] },
    moduleNameMapper: {
        "^react(-dom)?$": "<rootDir>/frontend/js/preact-compat.ts",
        "^react-dom/client$": "preact/compat/client",
        "^react/jsx-runtime$": "preact/jsx-runtime",
    },
};
