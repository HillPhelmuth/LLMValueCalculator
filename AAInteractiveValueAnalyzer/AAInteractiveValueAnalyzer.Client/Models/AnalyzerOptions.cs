using System.ComponentModel;
using System.Reflection;

namespace AAInteractiveValueAnalyzer.Client.Models;

public enum TaskCategoryOption
{
    [Description("Extraction")]
    Extraction,

    [Description("Classification / routing")]
    ClassificationRouting,

    [Description("Summarization")]
    Summarization,

    [Description("Code generation")]
    CodeGeneration,

    [Description("Agentic workflow")]
    AgenticWorkflow,

    [Description("Drafting / writing")]
    DraftingWriting,

    [Description("Research / analysis")]
    ResearchAnalysis,

    [Description("Other")]
    Other
}

public enum DifficultySensitivityOption
{
    [Description("Soft")]
    Soft,

    [Description("Normal")]
    Normal,

    [Description("Sharp")]
    Sharp
}

public enum ContextRequirementOption
{
    [Description("Short, clean context")]
    ShortClean,

    [Description("Medium, mostly relevant context")]
    MediumMostlyRelevant,

    [Description("Large but clean context")]
    LargeClean,

    [Description("Large and noisy context")]
    LargeNoisy,

    [Description("Very large, noisy, or cross-document context")]
    VeryLargeNoisyCrossDocument
}

public enum ReasoningDepthOption
{
    [Description("Single-step transformation")]
    SingleStepTransformation,

    [Description("Light reasoning")]
    Light,

    [Description("Moderate multi-step reasoning")]
    ModerateMultiStep,

    [Description("Deep conditional reasoning")]
    DeepConditional,

    [Description("Research-grade synthesis or planning")]
    ResearchGradeSynthesisPlanning
}

public enum DomainSpecificityOption
{
    [Description("General knowledge")]
    GeneralKnowledge,

    [Description("Some domain-specific terminology")]
    SomeDomainSpecificTerminology,

    [Description("Specialized professional domain")]
    SpecializedProfessionalDomain,

    [Description("Expert or regulated domain")]
    ExpertOrRegulatedDomain
}

public enum ToolUseOption
{
    [Description("No tool use")]
    None,

    [Description("One or two deterministic tools")]
    OneOrTwoDeterministicTools,

    [Description("Multiple tools with validation")]
    MultipleToolsWithValidation,

    [Description("Autonomous tool sequence")]
    AutonomousToolSequence,

    [Description("Agentic workflow with irreversible actions")]
    AgenticWorkflowWithIrreversibleActions
}

public enum VerifiabilityOption
{
    [Description("Deterministically testable")]
    DeterministicallyTestable,

    [Description("Mostly verifiable by reviewer")]
    MostlyVerifiableByReviewer,

    [Description("Partly subjective")]
    PartlySubjective,

    [Description("Hard to detect wrong answers")]
    HardToDetectWrongAnswers
}

public enum OutputConstraintOption
{
    [Description("Free text")]
    FreeText,

    [Description("Structured JSON or schema")]
    StructuredJsonOrSchema,

    [Description("Code, SQL, or executable artifact")]
    CodeSqlOrExecutableArtifact,

    [Description("External-facing or regulated artifact")]
    ExternalFacingOrRegulatedArtifact
}

public static class AnalyzerOptionDisplay
{
    public static IReadOnlyList<TEnum> Values<TEnum>() where TEnum : struct, Enum
    {
        return Enum.GetValues<TEnum>();
    }

    public static string DisplayName<TEnum>(this TEnum value) where TEnum : struct, Enum
    {
        var member = typeof(TEnum).GetMember(value.ToString()).FirstOrDefault();
        var description = member?.GetCustomAttribute<DescriptionAttribute>();
        return description?.Description ?? value.ToString();
    }
}